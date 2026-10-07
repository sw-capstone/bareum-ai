import unicodedata
import unittest
from pathlib import Path

import pymupdf
from parser_checks import requires_corpus, source_records

from bareum_ai.dataset.parsing import parser as P


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
        kept = P.table_drawings([glyph, dashed, box, outside], [word])
        self.assertEqual(kept, [dashed, box, outside])

    def test_gradient_strips_need_touching_run_and_changing_color(self):
        def strip(x, shade):
            return drawing("f", (x, 0, x + 8.5, 46), ["l"], fill=(shade, shade, shade))

        gradient = [strip(10 + i * 8.5, 0.9 + i * 0.01) for i in range(10)]
        same = [strip(200 + i * 8.5, 0.9) for i in range(10)]
        apart = [strip(400 + i * 12, 0.9 + i * 0.01) for i in range(10)]
        strips = P.gradient_strips(gradient + same + apart)
        self.assertEqual(strips, set(range(10)))

    @requires_corpus
    def test_title_gradient_is_not_a_table(self):
        # 제목 줄 배경 그러데이션(가는 사각형 50개)이 51열짜리 표로 잡혔던 문서다.
        record = next(
            r
            for r in source_records()
            if "SNS 홍보채널 운영 계획_B" in unicodedata.normalize("NFC", r["pdf_path"])
        )
        doc = P.parse_pdf(Path(record["pdf_path"]))[0]
        first = doc["blocks"][:2]
        self.assertEqual(
            [(b["kind"], b["text"]) for b in first],
            [
                ("document_title", "2021년 기관 SNS 홍보채널 운영 계획"),
                ("heading", "사업 개요"),
            ],
        )
        schedule = next(
            b
            for b in doc["blocks"]
            if b["kind"] == "table" and b["rows"][0] == ["내용", "기간"]
        )
        self.assertIn("gradient_columns_removed", schedule["transformations"])
        self.assertEqual(len(schedule["rows"][0]), 2)

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
