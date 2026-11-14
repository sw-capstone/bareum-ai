"""#26 좌우 바깥선 없는 표와 채움 다각형 괘선으로 그린 표의 복원."""

import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

import pymupdf
from parser_checks import requires_corpus, source_records

from bareum_ai.dataset.parsing import parser as P


def parse_drawn(draw):
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / "table.pdf"
        with pymupdf.open() as doc:
            page = doc.new_page()
            draw(page)
            doc.save(path)
        return P.parse_pdf(path)[0]


def open_table(page, filled=False):
    """가로선 4개와 안쪽 세로선 1개만 있는 표. filled 면 괘선을 가는 채움 사각형으로 그린다."""
    def rule(a, b):
        if filled:
            rect = pymupdf.Rect(min(a[0], b[0]) - 0.4, min(a[1], b[1]) - 0.4,
                                max(a[0], b[0]) + 0.4, max(a[1], b[1]) + 0.4)
            page.draw_polyline([rect.tl, rect.tr, rect.br, rect.bl, rect.tl], color=None, fill=(0, 0, 0))
        else:
            page.draw_line(a, b)

    for y in (100, 130, 160, 190):
        rule((60, y), (500, y))
    rule((200, 100), (200, 190))
    for y, (left, right) in zip((120, 150, 180), (("구분", "내용"), ("위치", "김제시 용지면"), ("기간", "2019년 ~ 2021년"))):
        page.insert_text((70, y), left, fontname="korea")
        page.insert_text((210, y), right, fontname="korea")
    page.insert_text((60, 300), "본문 문단입니다.", fontname="korea")


class UnborderedTableTests(unittest.TestCase):
    def test_open_sided_table_is_recovered(self):
        for filled in (False, True):
            with self.subTest(filled=filled):
                doc = parse_drawn(lambda page: open_table(page, filled))
                tables = [b for b in doc["blocks"] if b["kind"] == "table"]
                self.assertEqual(len(tables), 1)
                self.assertIn("unbordered_table_recovered", tables[0]["transformations"])
                self.assertEqual(tables[0]["rows"], [["구분", "내용"], ["위치", "김제시 용지면"], ["기간", "2019년 ~ 2021년"]])
                self.assertFalse([w for w in doc["warnings"] if w["code"] == "table_unbordered_candidate"])

    def test_title_box_with_text_on_one_side_is_not_table(self):
        def draw(page):
            for y in (100, 103, 140):
                page.draw_line((60, y), (500, y))
            page.draw_line((90, 103), (90, 140))
            page.insert_text((100, 128), "운영체계 개선계획", fontname="korea")

        doc = parse_drawn(draw)
        self.assertFalse([b for b in doc["blocks"] if b["kind"] == "table"])
        self.assertFalse([w for w in doc["warnings"] if w["code"] == "table_unbordered_candidate"])

    def test_thin_filled_bar_is_rule_but_glyph_box_is_not(self):
        bar = {"type": "f", "rect": pymupdf.Rect(100, 50, 101, 90), "items": []}
        glyph = {"type": "f", "rect": pymupdf.Rect(100, 50, 110, 62), "items": []}
        self.assertEqual((P.thin_bar(bar), P.thin_bar(glyph)), ("v", None))

    def test_light_gradient_slice_is_not_rule(self):
        # 그라데이션 배경은 연한 색의 가는 띠 수백 개로 칠해진다.
        dark = {"type": "f", "fill": (0.4, 0.4, 1.0), "rect": pymupdf.Rect(100, 50, 101, 90), "items": []}
        light = {"type": "f", "fill": (0.7, 0.9, 1.0), "rect": pymupdf.Rect(100, 50, 101, 90), "items": []}
        self.assertEqual((P.thin_bar(dark), P.thin_bar(light)), ("v", None))


@requires_corpus
class UnborderedSourceTests(unittest.TestCase):
    @staticmethod
    @lru_cache
    def parse(identity):
        record = next(r for r in source_records() if r["document_id"] == identity)
        return P.parse_pdf(Path(record["pdf_path"]))[0]

    def recovered(self, identity, page):
        return [b for b in self.parse(identity)["blocks"]
                if b["page"] == page and "unbordered_table_recovered" in (b.get("transformations") or [])]

    def test_wetland_project_table(self):
        tables = self.recovered("doc_8f8152f2e456ee9f", 1)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["rows"][0], ["구 분", "내 용", "비 고"])
        self.assertEqual(len(tables[0]["rows"]), 6)

    def test_filled_rule_tables(self):
        tables = self.recovered("doc_ebaf5c63866d198d", 3)
        self.assertEqual([t["rows"][0][0] for t in tables], ["구 분", "직능", "구분", "구분"])

    def test_columns_are_not_merged(self):
        # 원래 페이지에서 찾으면 '매우 그렇다·만족·보통'이 한 칸으로 합쳐진다.
        doc = self.parse("doc_31668196b750198b")
        self.assertFalse([b for b in doc["blocks"] if b["kind"] == "table" and "매우 만족 보통 그렇다" in b["text"]])
        table = next(t for t in self.recovered("doc_31668196b750198b", 12) if t["rows"][0][0] == "평가항목")
        self.assertEqual(table["rows"][1], ["(1) 직원들은 친절히 응대한다", "①", "②", "③", "④", "⑤", "⑥"])

    def test_filled_page_table_is_found_on_rules_only(self):
        # 채움 도형이 많아 원래 페이지에서는 같은 괘선으로도 표를 찾지 못한다.
        table = self.recovered("doc_4911432da30f3fc4", 2)[0]
        self.assertEqual(table["rows"][2], ["벽면이용간판 (가로·세로)", "91개", "87개", "-", "4개", ""])

    def test_gradient_header_table_keeps_header_and_first_column(self):
        table = self.recovered("doc_9d949f66ef7c0e47", 15)[0]
        self.assertEqual(table["rows"][0], ["동 별", "학 교 명", "소 재 지", "개방 면수", "개방장소", "비 고"])

    def test_middle_fragment_table_is_merged_into_recovered_table(self):
        tables = self.recovered("doc_8218a7de95b5a25c", 3)
        self.assertEqual(tables[0]["rows"][1], ["3,815", "1,092", "857", "235", "2,723", "28.6%"])
        self.assertFalse([b for b in self.parse("doc_8218a7de95b5a25c")["blocks"]
                          if b["kind"] == "table" and b["text"].startswith("(B=C+D)")])


if __name__ == "__main__":
    unittest.main()
