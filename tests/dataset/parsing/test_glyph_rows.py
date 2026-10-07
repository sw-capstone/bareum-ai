import unittest
from pathlib import Path

import pymupdf
from parser_checks import requires_corpus, source_records

from bareum_ai.dataset.parsing import parser as P


class GlyphPage:
    def __init__(self, drawings):
        self.drawings = drawings

    def get_drawings(self):
        return self.drawings


def drawing(kind, rect, items, fill=(0, 0, 0)):
    return {
        "type": kind,
        "rect": pymupdf.Rect(rect),
        "items": [(item,) for item in items],
        "fill": fill if "f" in kind else None,
    }


class GlyphDrawingTests(unittest.TestCase):
    def test_only_filled_glyph_outlines_are_removed(self):
        word = P.Line(
            "가나", "가나", (10, 10, 30, 22), 12, [P.Word("가나", (10, 10, 30, 22))]
        )
        glyph = drawing("fs", (11, 11, 19, 21), ["l", "c", "c"])
        dashed = drawing("s", (12, 21, 12.4, 21), ["l"])
        box = drawing("f", (11, 11, 19, 21), ["re"])
        outside = drawing("fs", (50, 11, 58, 21), ["l", "c"])
        kept = P.table_drawings(GlyphPage([glyph, dashed, box, outside]), [word])
        self.assertEqual(kept, [dashed, box, outside])

    @requires_corpus
    def test_schedule_rows_split_by_ruled_lines(self):
        # 글자 윤곽선의 짧은 가로 획 때문에 '10월 초순'과 '10월 내' 행이 합쳐졌던 표다.
        record = next(
            r for r in source_records() if r["document_id"] == "doc_8c7a3d98a5a5319e"
        )
        doc = P.parse_pdf(Path(record["pdf_path"]))[0]
        table = next(
            b
            for b in doc["blocks"]
            if b["kind"] == "table" and b["rows"] and b["rows"][0] == ["내용", "기간"]
        )
        self.assertEqual(
            table["rows"],
            [
                ["내용", "기간"],
                ["원가계산 정리", "9. 21.~ 9. 24."],
                ["공개입찰 서류정리", "9. 28. ~ 9. 30."],
                ["공개입찰 공고/마감", "10월 초순"],
                ["제안서 평가 및 선정 공지", "10월 내"],
            ],
        )
        self.assertIn("glyph_merged_rows_split", table["transformations"])
