import json
import signal
import sys
from pathlib import Path

from pydantic import TypeAdapter

from bareum_ai.dataset.parsing.parser import parse_pdf
from bareum_ai.pdf_parsing.parser import parser_worker
from bareum_ai.pdf_parsing.types import JsonObject


def parse_overlapping_table(path: Path) -> tuple[JsonObject, str, str]:
    payload, markdown, raw = parse_pdf(path)
    parsed = TypeAdapter(JsonObject).validate_json(json.dumps(payload))
    parsed["blocks"] = [
        {
            "id": "table1",
            "page": 1,
            "kind": "table",
            "text": "Merged header",
            "source_text": "Merged header",
            "bbox": [0, 0, 100, 100],
            "rows": [["Merged", "Header"], ["", "Value"]],
            "cells": [
                {"row": 0, "col": 0, "rowspan": 2, "colspan": 2},
                {"row": 1, "col": 1, "rowspan": 1, "colspan": 1},
            ],
        }
    ]
    return parsed, markdown, raw


def parse_exception(_path: Path) -> tuple[JsonObject, str, str]:
    sensitive = (
        "private-parser-content example-internal-token "
        "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
        "reports/101/original.pdf?signature=example"
    )
    sys.stdout.write(sensitive)
    sys.stderr.write(sensitive)
    raise RuntimeError(sensitive)


def parse_invalid_schema(path: Path) -> tuple[JsonObject, str, str]:
    payload, markdown, raw = parse_pdf(path)
    parsed = TypeAdapter(JsonObject).validate_json(json.dumps(payload))
    del parsed["pages"]
    return parsed, markdown, raw


def parse_invalid_references(path: Path) -> tuple[JsonObject, str, str]:
    payload, markdown, raw = parse_pdf(path)
    parsed = TypeAdapter(JsonObject).validate_json(json.dumps(payload))
    parsed["document"] = {"page_count": 1, "sha256": "0" * 64}
    return parsed, markdown, raw


def main() -> int:
    manifest = Path(sys.argv[1])
    job = json.loads(manifest.read_bytes())
    mode = job["originalFilename"].removesuffix("-private-filename.pdf")
    if mode == "overlapping-table":
        parser_worker.parse_pdf = parse_overlapping_table
        return parser_worker.main()
    if mode == "parser-exception":
        parser_worker.parse_pdf = parse_exception
        return parser_worker.main()
    if mode == "invalid-schema":
        parser_worker.parse_pdf = parse_invalid_schema
        return parser_worker.main()
    if mode == "invalid-references":
        parser_worker.parse_pdf = parse_invalid_references
        return parser_worker.main()
    if mode == "crash":
        return 7
    if mode == "missing-result":
        return 0
    if mode == "invalid":
        manifest.with_name("result.json").write_text('{"status":"SUCCEEDED"}')
        return 0
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    sys.stdout.write("READY\n")
    sys.stdout.flush()
    signal.pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
