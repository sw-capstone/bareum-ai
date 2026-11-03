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
import urllib.error

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
    run_id = f"{created:%Y%m%dT%H%M%S%fZ}-{labeler.id}"
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
    manifest["status"] = "running"
    manifest["documents"] = [
        {"original_path": str(path), "status": "pending", "failed_units": None, "usage": {}}
        for path in paths
    ]

    def checkpoint() -> None:
        # 같은 디렉터리에서 교체해 쓰는 도중 중단되어도 이전 JSON은 보존한다.
        temporary = run_dir / "manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(run_dir / "manifest.json")

    checkpoint()  # 첫 문서를 읽거나 API를 부르기 전 실행 설정과 전체 대상을 남긴다.
    current = None
    try:
        for path, current in zip(paths, manifest["documents"]):
            current.update(status="running", started_at=datetime.now(UTC).isoformat())
            checkpoint()
            doc = json.loads(path.read_text(encoding="utf-8"))
            frozen_path, frozen = snapshot.freeze(doc, run_dir / "snapshots")
            document_id = doc["document"]["document_id"]
            current.update(path=str(frozen_path), document_id=document_id,
                           parsed_sha256=snapshot.content_hash(doc), snapshot=frozen)
            checkpoint()
            doc_dir = run_dir / "documents" / document_id
            doc_dir.mkdir(parents=True)

            def log(record: dict) -> None:
                calls.write(json.dumps(record, ensure_ascii=False) + "\n")
                calls.flush()
                for key, value in _flat_usage(record.get("usage")).items():
                    current["usage"][key] = current["usage"].get(key, 0) + value
                current["last_call"] = {k: record[k] for k in ("request_id", "attempt", "error") if k in record}
                checkpoint()

            with (doc_dir / "calls.jsonl").open("w", encoding="utf-8") as calls:
                result = label_document(doc, labeler, {"run_id": run_id, "labeler": labeler_meta}, log=log)
            (doc_dir / "annotations.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            current.update(status="completed", failed_units=len(result["failed_units"]),
                           finished_at=datetime.now(UTC).isoformat())
            checkpoint()
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        status = "interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed"
        cause = error.__cause__
        # 응답 본문·인증 정보가 담길 수 있는 예외 문자열은 manifest에 복사하지 않는다.
        failure = {"type": type(error).__name__}
        if cause is not None:
            failure["cause_type"] = type(cause).__name__
            if isinstance(cause, urllib.error.HTTPError):
                failure["http_status"] = cause.code
        manifest.update(status=status, error=failure, finished_at=datetime.now(UTC).isoformat())
        if current is not None and current["status"] == "running":
            current.update(status=status, error=failure, finished_at=manifest["finished_at"])
        checkpoint()
        raise
    manifest.update(status="completed", finished_at=datetime.now(UTC).isoformat())
    checkpoint()
    return manifest
