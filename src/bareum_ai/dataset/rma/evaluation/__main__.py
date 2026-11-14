"""RMA 파일럿 평가. 사용법은 python -m bareum_ai.dataset.rma.evaluation --help."""

# ─── How to run ───
# 저장소 루트에서 uv run python -m bareum_ai.dataset.rma.evaluation --help
# ──────────────────

import argparse
import json
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter

from bareum_ai import specs
from bareum_ai.dataset.rma.evaluation.agreement import human_agreement, repeatability, summarize
from bareum_ai.dataset.rma.evaluation.evidence import evaluate_evidence
from bareum_ai.dataset.rma.evaluation.inputs import load_gold, load_human, load_pilot, load_run
from bareum_ai.dataset.rma.evaluation.models import UNDETERMINED, EvidenceReview, InputError, Report
from bareum_ai.dataset.rma.evaluation.scoring import rate, score
from bareum_ai.dataset.rma.review import Workspace

SCHEMA: Final = "rma-evaluation-1"


def _reviews(path: Path | None) -> dict[str, dict[str, EvidenceReview]]:
    if path is None:
        return {}
    result: dict[str, dict[str, EvidenceReview]] = {}
    for entry in TypeAdapter(list[EvidenceReview]).validate_json(path.read_bytes()):
        current = result.setdefault(entry.run_id, {})
        if entry.evidence_id in current or not entry.reason.strip():
            raise InputError(f"근거 검수 ID가 중복되거나 사유가 없습니다: {entry.run_id}/{entry.evidence_id}")
        current[entry.evidence_id] = entry
    return result


def evaluate(
    work: Path, *, annotators: tuple[str, str] | None = None, gold_name: str | None = None,
    run_paths: tuple[Path, ...] = (), exclusions: Path | None = None, evidence_reviews: Path | None = None,
) -> Report:
    """선정 범위를 평가하고 한 개의 JSON 보고서에 들어갈 지표와 사례를 반환한다."""
    if bool(gold_name) != bool(run_paths) or (not run_paths and annotators is None):
        raise InputError("독립 라벨러 두 명 또는 확정 정답과 모델 실행을 지정하세요")
    if annotators and (annotators[0] == annotators[1] or gold_name in annotators):
        raise InputError("독립 라벨러 두 명은 서로 달라야 하며 확정 정답 이름과도 달라야 합니다")

    ws = Workspace(work)
    pilot = load_pilot(ws, exclusions)
    catalog = specs.load("moves")["moves"]
    moves = sorted(set(catalog) - {UNDETERMINED})
    steps = [(move, step) for move in moves for step in sorted(catalog[move].get("steps") or {})]
    unreadable = [u for u in pilot.units if u.input_unreadable is not None]
    report: Report = {
        "schema_version": SCHEMA, "undefined_metric": "N/A (null)",
        "pilot": {
            "document_hashes": pilot.hashes, "specs": pilot.specs, "selected_units": len(pilot.units),
            "selected_blocks": sum(u.kind == "block" for u in pilot.units),
            "selected_table_rows": sum(u.kind == "table_row" for u in pilot.units),
            "eligible_units": sum(u.eligible for u in pilot.units),
            "input_unreadable": {**rate(len(unreadable), len(pilot.units)),
                                 "cases": [{**u.report(), "reason": u.input_unreadable} for u in unreadable]},
            "pre_exclusions": [{**u.report(), "reason": u.exclusion} for u in pilot.units if u.exclusion],
        },
    }
    if annotators:
        left, right = (load_human(ws, pilot, name) for name in annotators)
        report["human_agreement"] = {"annotators": list(annotators),
                                     **human_agreement(pilot.units, left, right, moves)}
    reviews = _reviews(evidence_reviews)
    used: set[tuple[str, str]] = set()
    if gold_name:
        gold = load_gold(ws, pilot, gold_name)
        runs = [load_run(ws, pilot, path) for path in run_paths]
        scores: list[Report] = []
        results: list[Report] = []
        for run in runs:
            run_id = run.manifest.run_id
            result: Report = {"manifest": run.manifest.model_dump(mode="json"),
                              "incompatibilities": list(run.incompatibilities), "scores": None}
            if not run.incompatibilities:
                scores_for_run = score(pilot, gold, run.labels, moves, steps)
                evidence, matched = evaluate_evidence(pilot, run.labels, reviews.get(run_id, {}))
                used.update((run_id, key) for key in matched)
                scores_for_run["evidence"] = evidence
                result["scores"] = scores_for_run
                scores.append(scores_for_run)
            results.append(result)
        report["gold_annotator"] = gold_name
        report["runs"] = results
        repeated = repeatability(pilot, gold, runs, {move for move, _ in steps})
        report["summary"] = summarize(scores) if repeated["comparable"] else None
        report["repeatability"] = repeated
    unknown = {(run_id, key) for run_id, entries in reviews.items() for key in entries} - used
    if unknown:
        run_id, key = min(unknown)
        raise InputError(f"보고서에 없는 실행 또는 근거 검수 ID: {run_id}/{key}")
    return report


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="고정 파일럿의 RMA 모델 성능, 사람 간 일치도, 3회 실행 일관성을 평가합니다.")
    cli.add_argument("--work", type=Path, required=True, help="pilot.json이 있는 고정 파일럿 폴더")
    cli.add_argument("--annotators", nargs=2, metavar=("LEFT", "RIGHT"), help="조정 전 labels/ 아래 독립 라벨러 이름 두 개")
    cli.add_argument("--gold", help="final/ 아래 확정 정답 이름 (예: adjudicated)")
    cli.add_argument("--run", action="append", type=Path, default=[], help="manifest.json이 있는 실행 폴더. 반복 지정")
    cli.add_argument("--exclusions", type=Path, help="사전 제외 목록 JSON: [{document_id, block_id, row?, reason}]")
    cli.add_argument("--evidence-reviews", type=Path,
                     help="사람 근거 검수 JSON: [{run_id, evidence_id, verdict, reason}]")
    cli.add_argument("--out", type=Path, required=True, help="새 보고서 JSON 경로. 정의되지 않은 지표는 null로 저장")
    args = cli.parse_args(argv)

    try:
        report = evaluate(
            args.work, annotators=(args.annotators[0], args.annotators[1]) if args.annotators else None,
            gold_name=args.gold, run_paths=tuple(args.run), exclusions=args.exclusions,
            evidence_reviews=args.evidence_reviews,
        )
        payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(payload + "\n")
    except (InputError, OSError, ValueError, KeyError) as error:
        cli.error(str(error))
    print(f"평가 보고서 저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
