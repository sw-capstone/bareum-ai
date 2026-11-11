"""독립 라벨러 간 일치도와 세 모델 실행의 예측 일관성."""

from collections.abc import Callable, Sequence
from statistics import mean
from typing import Final

from pydantic import JsonValue

from bareum_ai.dataset.rma.evaluation.models import (
    UNDETERMINED, Judgment, Labels, Pilot, Report, Run, Unit,
)
from bareum_ai.dataset.rma.evaluation.scoring import Sample, errors, primary_applicable, rate, ratio


def _kappa(move: str, pairs: Sequence[tuple[Judgment, Judgment]]) -> Report:
    total = len(pairs)
    left = sum(move in a.moves for a, _ in pairs)
    right = sum(move in b.moves for _, b in pairs)
    agreed = sum((move in a.moves) == (move in b.moves) for a, b in pairs)
    chance = left * right + (total - left) * (total - right)
    return {"move": move, "left_positive": left, "right_positive": right, "total": total,
            "kappa": ratio(agreed * total - chance, total * total - chance)}


def human_agreement(units: Sequence[Unit], left: Labels, right: Labels, moves: Sequence[str]) -> Report:
    pairs = [(u, left.at(u.key), right.at(u.key)) for u in units if u.eligible]
    valid = [(a, b) for _, a, b in pairs if a.status == b.status == "ok"]
    confirmed = [(a, b) for a, b in valid if a.steps_confirmed and b.steps_confirmed]
    unconfirmed = [(a, b) for a, b in valid if not (a.steps_confirmed and b.steps_confirmed)]
    differences: list[Report] = []
    for unit, a, b in pairs:
        axes = []
        if a.status != "ok" or b.status != "ok":
            axes.append("missing_list")
        else:
            if a.moves != b.moves:
                axes.append("move")
            if a.steps != b.steps:
                axes.append("step")
            if a.step_states != b.step_states:
                axes.append("step_status")
        if axes:
            differences.append({**unit.report(), "differences": axes, "left": a.report(), "right": b.report()})

    def steps(group: Sequence[tuple[Judgment, Judgment]], denominator: int) -> Report:
        return {
            "pairs": rate(sum(a.steps == b.steps for a, b in group), denominator),
            "status": rate(sum(a.step_states == b.step_states for a, b in group), denominator),
        }

    return {
        "move_exact": rate(sum(a.moves == b.moves for a, b in valid), len(pairs)),
        "both_undetermined": sum(UNDETERMINED in a.moves and UNDETERMINED in b.moves for a, b in valid),
        "kappa": [_kappa(move, valid) for move in [*moves, UNDETERMINED]],
        "missing_list_units": len(pairs) - len(valid),
        "step": {"overall": steps(valid, len(pairs)), "both_confirmed": steps(confirmed, len(confirmed)),
                 "unconfirmed": steps(unconfirmed, len(unconfirmed))},
        "differences": differences,
    }


def _conditions(runs: Sequence[Run], pilot: Pilot) -> list[str]:
    if len(runs) != 3:
        return ["requires_three_runs"]
    manifests = [r.manifest for r in runs]
    first = manifests[0]
    reasons = [f"{r.manifest.run_id}:{reason}" for r in runs for reason in r.incompatibilities]
    if len({m.run_id for m in manifests}) != 3:
        reasons.append("requires_distinct_runs")
    if any(m.status != "completed" for m in manifests):
        reasons.append("run_not_completed")
    for field in ("labeler", "prompt_sha256", "specs", "policy"):
        if any(getattr(m, field) != getattr(first, field) for m in manifests[1:]):
            reasons.append(f"different_{field}")
    documents = [{d.document_id: d.parsed_sha256 for d in m.documents} for m in manifests]
    if any(None in docs or not pilot.hashes.keys() <= docs.keys() for docs in documents):
        reasons.append("pilot_documents_missing")
    if any(docs.keys() != documents[0].keys() for docs in documents[1:]):
        reasons.append("different_document_sets")
    elif any(docs != documents[0] or not all(docs.values()) for docs in documents):
        reasons.append("different_parsed_inputs")
    return reasons


def _moves(judgment: Judgment) -> JsonValue:
    return sorted(a.move for a in judgment.annotations)


def _move_steps(judgment: Judgment) -> JsonValue:
    return [[move, list(steps)] for move, steps in sorted(
        (a.move, tuple(sorted(s.step for s in a.steps))) for a in judgment.annotations
    )]


def _primary(judgment: Judgment) -> JsonValue:
    return judgment.primary_move


def _states(judgment: Judgment) -> JsonValue:
    return [[m, status] for m, status in judgment.step_states]


_AXES: Final[dict[str, Callable[[Judgment], JsonValue]]] = {
    "moves": _moves, "annotation_move_steps": _move_steps, "primary_move": _primary, "step_status": _states,
}


def repeatability(pilot: Pilot, gold: Labels, runs: Sequence[Run], step_moves: set[str]) -> Report:
    units = [u for u in pilot.units if u.eligible]
    denominators = dict.fromkeys(_AXES, 0)
    matches = dict.fromkeys(_AXES, 0)
    variations: list[Report] = []
    repeated: list[Report] = []
    reasons = _conditions(runs, pilot)

    for unit in units:
        expected = gold.at(unit.key)
        applicable = {
            "moves": True, "annotation_move_steps": True,
            "primary_move": primary_applicable(expected),
            "step_status": bool(expected.moves & step_moves),
        }
        for name, applies in applicable.items():
            denominators[name] += applies
        if reasons:
            continue
        predictions = [run.labels.at(unit.key) for run in runs]
        valid = all(p.status == "ok" for p in predictions)
        changed = []
        for name, signature in _AXES.items():
            if not applicable[name]:
                continue
            values = [signature(p) for p in predictions]
            equal = values[0] == values[1] == values[2]
            matches[name] += valid and equal
            if not equal or len({p.status for p in predictions}) > 1:
                changed.append(name)
        detail = {**unit.report(), "gold": expected.report(),
                  "predictions": {r.manifest.run_id: p.report() for r, p in zip(runs, predictions, strict=True)}}
        if changed:
            variations.append({**detail, "differences": changed})
        first, *rest = [errors(Sample(unit, expected, p)) for p in predictions]
        if common := first.intersection(*rest):
            repeated.append({**detail, "errors_in_all_runs": sorted(common)})

    return {
        "comparable": not reasons, "reasons": reasons,
        "metrics": {
            name: ({"matched": None, "total": total, "rate": None} if reasons else rate(matches[name], total))
            for name, total in denominators.items()
        },
        "variation_cases": variations, "repeated_errors": repeated,
    }


_SCORE_FIELDS: Final = frozenset({"macro_f1", "precision", "recall", "f1", "rate"})


def _metric_values(report: JsonValue, prefix: str = "") -> list[tuple[str, float | None]]:
    """집계의 점수만 보고서 경로에 연결한다. 오류 사례나 건수는 집계하지 않는다."""
    values: list[tuple[str, float | None]] = []
    if isinstance(report, dict):
        for key, value in report.items():
            path = f"{prefix}.{key}" if prefix else key
            if key in _SCORE_FIELDS and (value is None or isinstance(value, float)):
                values.append((path, value))
            elif isinstance(value, dict):
                values.extend(_metric_values(value, path))
            elif key == "labels" and isinstance(value, list):
                for label in value:
                    if isinstance(label, dict):
                        name = label["label"]
                        label_key = "/".join(str(part) for part in name) if isinstance(name, list) else str(name)
                        values.extend(_metric_values(label, f"{path}.{label_key}"))
    return values


def summarize(scores: Sequence[Report]) -> Report:
    values: dict[str, list[float]] = {}
    for score in scores:
        for path, value in _metric_values(score):
            available = values.setdefault(path, [])
            if value is not None:
                available.append(value)
    return {
        path: {"runs": len(items), "mean": mean(items) if items else None,
               "min": min(items) if items else None, "max": max(items) if items else None}
        for path, items in values.items()
    }
