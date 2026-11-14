"""확정 정답 대비 모델 성능, 제외 사유와 오류 사례."""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean
from unicodedata import normalize

from bareum_ai.dataset.rma.evaluation.models import (
    UNDETERMINED, Judgment, Labels, Pair, Pilot, Report, Unit,
)


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def rate(matched: int, total: int) -> Report:
    return {"matched": matched, "total": total, "rate": ratio(matched, total)}


@dataclass(frozen=True, slots=True)
class Counts:
    matched: int = 0
    missing: int = 0
    added: int = 0

    def report(self) -> Report:
        tp, fn, fp = self.matched, self.missing, self.added
        return {
            "matched": tp, "missing": fn, "added": fp, "support": tp + fn,
            "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
            "f1": ratio(2 * tp, 2 * tp + fp + fn),
        }


def classification[T: (str, Pair)](
    samples: Iterable[tuple[set[T], set[T]]], catalog: Iterable[T],
) -> Report:
    matched: Counter[T] = Counter()
    missing: Counter[T] = Counter()
    added: Counter[T] = Counter()
    for expected, actual in samples:
        matched.update(expected & actual)
        missing.update(expected - actual)
        added.update(actual - expected)
    labels = sorted(set(catalog) | matched.keys() | missing.keys() | added.keys())
    rows = [{"label": label if isinstance(label, str) else list(label),
             **Counts(matched[label], missing[label], added[label]).report()} for label in labels]
    scores = [f1 for row in rows if isinstance(f1 := row["f1"], float)]
    return {
        "labels": rows,
        "macro_f1": mean(scores) if scores else None,
        "macro_labels": len(scores),
        "micro": Counts(matched.total(), missing.total(), added.total()).report(),
    }


@dataclass(frozen=True, slots=True)
class Sample:
    unit: Unit
    gold: Judgment
    prediction: Judgment

    @property
    def move_scored(self) -> bool:
        return self.unit.eligible and self.gold.status == "ok" and UNDETERMINED not in self.gold.moves

    @property
    def step_scored(self) -> bool:
        return self.move_scored and self.gold.steps_confirmed

    @property
    def move_exact(self) -> bool:
        return self.prediction.status == "ok" and self.gold.moves == self.prediction.moves

    @property
    def predicted_moves(self) -> set[str]:
        return self.prediction.moves - {UNDETERMINED}


def primary_applicable(gold: Judgment) -> bool:
    return gold.status == "ok" and bool(gold.annotations) and UNDETERMINED not in gold.moves


def _normalized(value: str) -> str:
    return " ".join(normalize("NFC", value).split())


def _spec_items(judgment: Judgment) -> Counter[Pair]:
    return Counter((_normalized(item.spec_field if item.spec_field is not None else item.raw_label),
                    _normalized(item.value))
                   for annotation in judgment.annotations for item in annotation.spec_items)


def _spec_score(samples: Sequence[Sample]) -> Report:
    matched = missing = added = 0
    for sample in samples:
        expected, actual = _spec_items(sample.gold), _spec_items(sample.prediction)
        matched += (expected & actual).total()
        missing += (expected - actual).total()
        added += (actual - expected).total()
    return Counts(matched, missing, added).report()


def _step_status(samples: Sequence[Sample]) -> Report:
    matched = total = 0
    for sample in samples:
        for move in sample.gold.moves & sample.predicted_moves:
            expected = tuple(status for m, status in sample.gold.step_states if m == move)
            actual = tuple(status for m, status in sample.prediction.step_states if m == move)
            matched += expected == actual
            total += 1
    return rate(matched, total)


def _scores(samples: Sequence[Sample], moves: Sequence[str], steps: Sequence[Pair]) -> Report:
    move_samples = [s for s in samples if s.move_scored]
    step_samples = [s for s in move_samples if s.step_scored]
    unconfirmed = [s for s in move_samples if not s.step_scored]
    nonempty = [s for s in move_samples if s.gold.moves]
    empty = [s for s in move_samples if not s.gold.moves]
    primary = [s for s in nonempty if primary_applicable(s.gold)]
    roles = [s for s in samples if s.unit.eligible and s.gold.role is not None]
    move_errors = Counter((missing, added) for s in move_samples
                          for missing in s.gold.moves - s.predicted_moves
                          for added in s.predicted_moves - s.gold.moves)
    step_errors = Counter((missing, added) for s in step_samples
                          for missing in s.gold.steps - s.prediction.steps
                          for added in s.prediction.steps - s.gold.steps)
    return {
        "selected_units": len(samples), "move_scored_units": len(move_samples),
        "step_scored_units": len(step_samples), "step_unconfirmed_units": len(unconfirmed),
        "move": classification(((s.gold.moves, s.predicted_moves) for s in move_samples), moves),
        "step": classification(((s.gold.steps, s.prediction.steps) for s in step_samples), steps),
        "known_steps_in_unconfirmed_units": {
            "matched": sum(len(s.gold.steps & s.prediction.steps) for s in unconfirmed),
            "missing": sum(len(s.gold.steps - s.prediction.steps) for s in unconfirmed),
        },
        "move_exact": rate(sum(s.move_exact for s in move_samples), len(move_samples)),
        "move_exact_nonempty": rate(sum(s.move_exact for s in nonempty), len(nonempty)),
        "normal_empty": rate(sum(s.move_exact and s.prediction.role == s.gold.role for s in empty), len(empty)),
        "row_role": rate(sum(s.prediction.status == "ok" and s.prediction.role == s.gold.role for s in roles),
                         len(roles)),
        "primary_move": rate(sum(s.prediction.status == "ok" and s.gold.primary_move == s.prediction.primary_move
                                 for s in primary), len(primary)),
        "step_status": _step_status(move_samples),
        "spec": _spec_score(move_samples),
        "move_missing_added": [{"missing": a, "added": b, "units": n}
                               for (a, b), n in sorted(move_errors.items())],
        "step_missing_added": [{"missing": list(a), "added": list(b), "units": n}
                               for (a, b), n in sorted(step_errors.items())],
    }


def _statuses(samples: Sequence[Sample]) -> Report:
    total = len(samples)
    unreadable = [s for s in samples if s.unit.input_unreadable is not None]
    eligible = [s for s in samples if s.unit.input_unreadable is None]
    failures = [s for s in eligible if s.prediction.status in ("failed", "missing")]
    model_unreadable = [s for s in eligible if s.prediction.status == "unreadable"]
    hold = [s for s in eligible if UNDETERMINED in s.prediction.moves]
    return {
        "input_unreadable": {**rate(len(unreadable), total),
                             "reasons": dict(Counter(s.unit.input_unreadable for s in unreadable
                                                     if s.unit.input_unreadable is not None))},
        "model_unreadable": {**rate(len(model_unreadable), total),
                             "tables": len({(s.unit.key.document_id, s.unit.key.block_id) for s in model_unreadable}),
                             "reasons": dict(Counter(s.prediction.reason or "unspecified" for s in model_unreadable))},
        "model_undetermined": {**rate(len(hold), total), "reasons": dict(Counter(
            reason for s in hold for reason in {a.undetermined_reason or "unspecified"
                                                for a in s.prediction.annotations if a.move == UNDETERMINED}))},
        "response_failure": {**rate(len(failures), total),
                             "statuses": dict(Counter(s.prediction.status for s in failures)),
                             "reasons": dict(Counter(s.prediction.reason or "not_returned" for s in failures))},
    }


def _document_scores(pilot: Pilot, gold: Labels, predictions: Labels) -> Report:
    purpose = outline = topics = failures = 0
    differences: list[Report] = []
    rationales: list[Report] = []
    for doc_id in pilot.hashes:
        expected = gold.documents[doc_id]
        actual = predictions.documents.get(doc_id)
        g_topics = set(expected.document_topics) if expected.document_topics is not None else None
        p_topics = set(actual.document_topics) if actual and actual.document_topics is not None else None
        purpose_ok = actual is not None and expected.document_type == actual.document_type
        outline_ok = actual is not None and expected.outline_id == actual.outline_id
        topics_ok = actual is not None and g_topics == p_topics
        purpose += purpose_ok
        outline += outline_ok
        topics += topics_ok
        failures += actual is None
        if not (purpose_ok and outline_ok and topics_ok):
            differences.append({
                "document_id": doc_id, "document_type_match": purpose_ok, "outline_match": outline_ok,
                "topics_match": topics_ok, "topics_missing": sorted((g_topics or set()) - (p_topics or set())),
                "topics_added": sorted((p_topics or set()) - (g_topics or set())),
                "gold": expected.model_dump(mode="json"), "prediction": actual.model_dump(mode="json") if actual else None,
            })
        if actual:
            rationales.append({"document_id": doc_id, "document_type_reason": actual.document_type_reason,
                               "document_topics_reason": actual.document_topics_reason,
                               "outline_reason": actual.outline_reason})
    count = len(pilot.hashes)
    return {"document_type": rate(purpose, count), "outline": rate(outline, count),
            "topics": rate(topics, count), "response_failure": rate(failures, count),
            "differences": differences, "rationales_for_review": rationales}


def errors(sample: Sample) -> set[str]:
    if not sample.move_scored:
        return set()
    expected, actual = sample.gold, sample.prediction
    found = {f"move_missing:{move}" for move in expected.moves - sample.predicted_moves}
    found.update(f"move_added:{move}" for move in sample.predicted_moves - expected.moves)
    if sample.step_scored:
        found.update(f"step_missing:{move}/{step}" for move, step in expected.steps - actual.steps)
        found.update(f"step_added:{move}/{step}" for move, step in actual.steps - expected.steps)
    if actual.status != "ok":
        found.add(actual.status)
    if UNDETERMINED in actual.moves:
        found.add("model_undetermined")
    if expected.role != actual.role:
        found.add("row_role")
    if primary_applicable(expected) and actual.status == "ok" and expected.primary_move != actual.primary_move:
        found.add("primary_move")
    if _spec_items(expected) != _spec_items(actual):
        found.add("spec")
    for move in expected.moves & sample.predicted_moves:
        expected_states = tuple(status for m, status in expected.step_states if m == move)
        actual_states = tuple(status for m, status in actual.step_states if m == move)
        if expected_states != actual_states:
            found.add(f"step_status:{move}")
    return found


def score(pilot: Pilot, gold: Labels, predictions: Labels, moves: Sequence[str], steps: Sequence[Pair]) -> Report:
    samples = [Sample(u, gold.at(u.key), predictions.at(u.key)) for u in pilot.units]
    exclusions: list[Report] = []
    exclusion_counts: Counter[str] = Counter()
    cases: list[Report] = []
    for sample in samples:
        unit, expected = sample.unit, sample.gold
        if not sample.step_scored:
            reasons = [r for r in (unit.input_unreadable, unit.exclusion) if r is not None]
            scope = "move_and_step"
            if unit.eligible:
                if expected.status == "unreadable":
                    reasons = ["gold_table_unreadable", expected.reason or "unspecified"]
                elif UNDETERMINED in expected.moves:
                    reasons = sorted({a.undetermined_reason or "unspecified"
                                      for a in expected.annotations if a.move == UNDETERMINED})
                else:
                    scope = "step"
                    reasons = sorted({a.step_reason or a.step_status for a in expected.annotations
                                      if a.step_status in ("partial", "undetermined")})
            exclusion: Report = {
                **unit.report(), "scope": scope, "reasons": reasons, "gold": expected.report(),
                "prediction": sample.prediction.report(),
            }
            if scope == "step":
                known = expected.steps
                actual = sample.prediction.steps
                exclusion["known_steps"] = {
                    "matched": [list(pair) for pair in sorted(known & actual)],
                    "missing": [list(pair) for pair in sorted(known - actual)],
                }
            exclusions.append(exclusion)
            exclusion_counts.update(reasons)
        if found := errors(sample):
            cases.append({**unit.report(), "errors": sorted(found),
                          "gold": expected.report(), "prediction": sample.prediction.report()})
    return {
        "overall": _scores(samples, moves, steps),
        "blocks": _scores([s for s in samples if s.unit.kind == "block"], moves, steps),
        "table_rows": _scores([s for s in samples if s.unit.kind == "table_row"], moves, steps),
        "documents": _document_scores(pilot, gold, predictions),
        "processing": _statuses(samples),
        "exclusion_counts": dict(exclusion_counts),
        "exclusions": exclusions, "error_cases": cases,
    }
