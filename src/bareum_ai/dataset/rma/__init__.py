"""파서 출력 묶음에 RMA 라벨링을 실행하고 실행 기록을 남긴다.

실행 디렉터리 구성:
    manifest.json                       실행 설정과 문서별 결과 요약
    documents/<document_id>/annotations.json   rma-annotations-2
    documents/<document_id>/calls.jsonl        라벨러 호출마다 요청 id·시도·원 응답·검증 오류

결과물은 저장소에 넣지 않는다.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess

from bareum_ai.rma import prompt as P
from bareum_ai.rma import request as R
from bareum_ai.rma.runner import CONTEXT_MISSING_RATIO, MAX_RETRIES, label_document
from bareum_ai.dataset.parsing import snapshot


def git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parent,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def split_files(manifest_path: Path, parsed_root: Path, split: str) -> list[Path]:
    """분할 manifest의 PDF 경로를 파서 출력 JSON 경로로 바꾼다(보고서/<분류>/<이름>.pdf → <root>/<분류>/<이름>.json)."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [
        parsed_root / Path(*Path(f["path"]).parts[1:]).with_suffix(".json")
        for group in manifest["groups"]
        if group["split"] == split
        for f in group["files"]
    ]


def _flat_usage(usage: dict | None, prefix: str = "") -> dict[str, int]:
    """API usage의 정수 값을 'output_tokens_details.reasoning_tokens'처럼 펼친다."""
    out: dict[str, int] = {}
    for key, value in (usage or {}).items():
        if isinstance(value, dict):
            out.update(_flat_usage(value, f"{prefix}{key}."))
        elif type(value) is int:
            out[f"{prefix}{key}"] = value
    return out


def run(paths: list[Path], labeler, out_dir: Path) -> dict:
    created = datetime.now(UTC)
    run_id = f"{created:%Y%m%dT%H%M%SZ}-{labeler.id}"
    run_dir = out_dir / run_id
    run_dir.mkdir(parents=True)
    labeler_meta = {"type": getattr(labeler, "kind", "model"), "id": labeler.id, "settings": labeler.settings}
    manifest = {
        "run_id": run_id,
        "created_at": created.isoformat(),
        "git_sha": git_sha(),
        "labeler": labeler_meta,
        "prompt_sha256": P.prompt_sha256(),
        "specs": R.spec_hashes(),
        "policy": {"max_retries": MAX_RETRIES, "context_missing_ratio": CONTEXT_MISSING_RATIO},
        "documents": [],
    }
    for path in paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        frozen_path, frozen = snapshot.freeze(doc, run_dir / "snapshots")
        document_id = doc["document"]["document_id"]
        doc_dir = run_dir / "documents" / document_id
        doc_dir.mkdir(parents=True)
        usage: dict[str, int] = {}

        def log(record: dict) -> None:
            calls.write(json.dumps(record, ensure_ascii=False) + "\n")
            for key, value in _flat_usage(record.get("usage")).items():
                usage[key] = usage.get(key, 0) + value

        with (doc_dir / "calls.jsonl").open("w", encoding="utf-8") as calls:
            result = label_document(doc, labeler, {"run_id": run_id, "labeler": labeler_meta}, log=log)
        (doc_dir / "annotations.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        manifest["documents"].append(
            {
                "path": str(frozen_path),
                "original_path": str(path),
                "document_id": document_id,
                "parsed_sha256": snapshot.content_hash(doc),
                "snapshot": frozen,
                "failed_units": len(result["failed_units"]),
                "usage": usage,
            }
        )
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return manifest
