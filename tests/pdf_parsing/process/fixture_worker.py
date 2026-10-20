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


def main() -> int:
    manifest = Path(sys.argv[1])
    job = json.loads(manifest.read_bytes())
    if job["originalFilename"] == "overlapping-table":
        parser_worker.parse_pdf = parse_overlapping_table
        return parser_worker.main()
    if job["originalFilename"] == "crash":
        return 7
    if job["originalFilename"] == "invalid":
        manifest.with_name("result.json").write_text('{"status":"SUCCEEDED"}')
        return 0
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    sys.stdout.write("READY\n")
    sys.stdout.flush()
    signal.pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
