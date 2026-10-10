"""고정 파싱 결과의 식별과 라벨의 원문 참조. 실행 통계는 입력 해시에서 제외한다."""

import copy
import hashlib
import json
from pathlib import Path

SNAPSHOT_SCHEMA = "parsed-snapshot-1"


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def content_hash(doc: dict) -> str:
    """좌표·표·제외 여부·설정·의존성까지 포함하고 실행 통계만 제외한다."""
    content = copy.deepcopy(doc)
    content.pop("snapshot", None)
    content.get("parser", {}).pop("stats", None)
    return hashlib.sha256(canonical(content)).hexdigest()


def legacy_content_hash(doc: dict) -> str:
    """기존 평가셋이 사용한 sections/blocks 해시도 검증한다."""
    return hashlib.sha256(canonical({"sections": doc["sections"], "blocks": doc["blocks"]})).hexdigest()


def manifest(doc: dict) -> dict:
    digest = content_hash(doc)
    parser = doc.get("parser", {})
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "snapshot_id": f"snap_{digest}",
        "document_id": doc["document"]["document_id"],
        "pdf_sha256": doc["document"]["sha256"],
        "parsed_content_sha256": digest,
        "parser": {"schema_version": doc["schema_version"],
                   **{k: copy.deepcopy(parser[k]) for k in
                      ("name", "source_hashes", "settings", "pymupdf_version") if k in parser}},
        "bbox_coordinate_system": "unrotated_pdf_points",
    }


def block_sources(doc: dict) -> dict:
    """블록 전체 원문을 블록당 한 번만 기록한다. span은 별도 text를 기준으로 한다."""
    return {
        b["id"]: {"page": b["page"], "bbox": copy.deepcopy(b["bbox"]),
                  "source_text": b["source_text"],
                  "text_sha256": hashlib.sha256(b.get("text", "").encode("utf-8")).hexdigest()}
        for b in doc["blocks"]
    }


def bind(doc: dict, labels: dict) -> dict:
    """고정 결과에 처음 연결할 때 사용한다. 기존 라벨의 재파싱 이전에는 사용하지 않는다."""
    return {**labels, "snapshot": manifest(doc), "block_sources": block_sources(doc),
            "span_basis": "block.text"}


def validate(doc: dict, labels: dict) -> None:
    expected = manifest(doc)
    actual = labels.get("snapshot")
    if actual != expected:
        raise ValueError("라벨의 파싱 스냅샷이 입력과 다릅니다. 재파싱 결과에 기존 라벨을 적용할 수 없습니다.")
    if labels.get("block_sources") != block_sources(doc):
        raise ValueError("라벨의 page/bbox/source_text 참조가 고정 파싱 결과와 다릅니다.")


def freeze(doc: dict, directory: Path) -> tuple[Path, dict]:
    """내용 해시별 디렉터리에 새 결과를 기록한다. 같은 ID의 다른 내용은 덮어쓰지 않는다."""
    meta = manifest(doc)
    target = directory / meta["snapshot_id"]
    target.mkdir(parents=True, exist_ok=True)
    parsed = target / "parsed.json"
    record = target / "manifest.json"
    # 배타적 생성으로 기존 파일/동시에 생성하는 작업의 결과를 덮어쓰지 않는다.
    for path, value in ((parsed, doc), (record, meta)):
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        except FileExistsError:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if (content_hash(existing) != meta["parsed_content_sha256"] if path == parsed else existing != meta):
                raise ValueError(f"고정 파싱 결과가 변조되었습니다: {path}")
    return parsed, meta


def load(directory: Path, meta: dict) -> dict:
    path = directory / meta["snapshot_id"] / "parsed.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    stored = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
    if stored != meta or manifest(doc) != meta:
        raise ValueError(f"고정 파싱 결과 해시가 일치하지 않습니다: {path}")
    return doc


def main() -> None:
    import argparse
    from .parser import parse_pdf

    cli = argparse.ArgumentParser(description="PDF 파싱 결과를 내용 해시별로 고정합니다")
    cli.add_argument("pdf", type=Path)
    cli.add_argument("-o", "--output", type=Path, required=True)
    args = cli.parse_args()
    doc, _, _ = parse_pdf(args.pdf)
    path, meta = freeze(doc, args.output)
    print(f"{meta['snapshot_id']}\n{path}")


if __name__ == "__main__":
    main()
