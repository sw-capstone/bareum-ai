"""RMA 라벨링 일괄 실행.

    # Dev 분할 전체 (API 키는 환경 변수 OPENAI_API_KEY)
    uv run python -m bareum_ai.dataset.rma --split dev --parsed-root <파서 출력 루트> --out <실행 결과 루트>

    # 파일을 직접 지정
    uv run python -m bareum_ai.dataset.rma <파서 출력 JSON …> --out <실행 결과 루트>
"""

import argparse
from pathlib import Path
import sys

from bareum_ai.dataset.rma import run, split_files
from bareum_ai.rma import prompt as P
from bareum_ai.rma.labelers import DEFAULT_MODEL, LabelerError, OpenAILabeler

DEFAULT_MANIFEST = Path("datasets/splits/manifest.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bareum_ai.dataset.rma", description="RMA 라벨링 일괄 실행")
    parser.add_argument("paths", nargs="*", type=Path, help="파서 출력 JSON")
    parser.add_argument("--split", choices=["dev", "test", "cbr"], help="분할 manifest로 대상 문서를 고른다")
    parser.add_argument("--parsed-root", type=Path, help="--split과 함께 쓰는 파서 출력 루트")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--limit", type=int, help="앞에서부터 이 수만큼만 실행한다(시험 실행)")
    parser.add_argument("--out", type=Path, required=True, help="실행 결과 루트. 저장소 밖을 권장한다")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--with-examples", action="store_true", help="카탈로그 경계·문맥 예시를 지시문에 붙인다(비교 실험용)")
    args = parser.parse_args(argv)
    P.INCLUDE_EXAMPLES = args.with_examples

    paths = list(args.paths)
    if args.split:
        if not args.parsed_root:
            parser.error("--split에는 --parsed-root가 필요합니다")
        paths += split_files(args.manifest, args.parsed_root, args.split)
    if args.limit:
        paths = paths[: args.limit]
    missing = [p for p in paths if not p.is_file()]
    if not paths or missing:
        parser.error(f"대상 파일이 없거나 찾을 수 없습니다: {missing[:5]}")

    try:
        labeler = OpenAILabeler(args.model, reasoning_effort=args.reasoning_effort)
        manifest = run(paths, labeler, args.out)
    except LabelerError as error:
        print(f"라벨러 오류: {error}", file=sys.stderr)
        return 2
    failed = sum(d["failed_units"] for d in manifest["documents"])
    print(f"{manifest['run_id']}: 문서 {len(manifest['documents'])}개, 실패 묶음 {failed}개 → {args.out / manifest['run_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
