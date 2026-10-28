"""#26 같은 행 처리: 읽기 순서, 로마 숫자 장 번호 결합, 어절 조각 결합."""

import re
import unittest
from functools import lru_cache
from pathlib import Path

import parser_checks as checks
from parser_checks import requires_corpus, source_records

from bareum_ai.dataset.parsing import parser as P


def line(text, box, size=10):
    words = []
    for m in re.finditer(r"\S+", text):
        width = (box[2] - box[0]) / max(len(text), 1)
        bbox = (box[0] + width * m.start(), box[1], box[0] + width * m.end(), box[3])
        words.append(P.Word(m.group(), bbox, m.start(), m.end()))
    return P.Line(text, text.strip(), box, size, words)


class RowOrderTests(unittest.TestCase):
    def test_slightly_higher_right_fragment_stays_after_left(self):
        # 오른쪽 조각이 0.4pt 높아도 같은 행이면 x 순서를 지킨다.
        left = line("우리구", (59, 165.0, 105, 183))
        right = line("관내 사업과 관련하여", (300, 164.6, 540, 183))
        rows = P.rows_of([right, left])
        self.assertEqual([[e.text for e in row] for row in rows], [["우리구", "관내 사업과 관련하여"]])

    def test_blocks_on_one_row_follow_x_order(self):
        title = P.Block("para", "행정사항", "행정사항", 12, (108, 682.0, 180, 694), size=12)
        number = P.Block("table", "Ⅲ", "Ⅲ", 12, (53, 682.4, 63, 694), size=12)
        self.assertEqual([b.text for b in P.reading_order([title, number])], ["Ⅲ", "행정사항"])

    def test_tall_block_does_not_pull_side_lines_into_one_row(self):
        figure = P.Block("figure", "", "", 1, (300, 100, 500, 400))
        texts = [P.Block("para", t, t, 1, (50, y, 250, y + 12), size=12)
                 for t, y in (("첫째", 120), ("둘째", 200))]
        self.assertEqual([b.text for b in P.reading_order([texts[1], figure, texts[0]])],
                         ["", "첫째", "둘째"])


class ChapterNumberTests(unittest.TestCase):
    def test_roman_number_joins_title_on_same_row(self):
        # 제목이 번호보다 0.4pt 높게 찍혀 먼저 추출돼도 번호와 결합한다.
        number = line("Ⅱ", (66.7, 433.0, 82.2, 449.0), 16)
        title = line(" 사업 개요", (102.6, 432.6, 187.8, 448.5), 16)
        joined = P.group_rows([title, number], P.Stats())
        self.assertEqual(len(joined), 1)
        block = P.classify(joined[0], 50, 1)
        self.assertEqual((block.kind, block.level, block.marker_raw, block.text), ("heading", 1, "Ⅱ", "사업 개요"))

    def test_arabic_number_join_is_unchanged(self):
        joined = P.group_rows([line("1", (10, 0, 15, 10)), line("사업개요", (30, 0, 70, 10))], P.Stats())
        self.assertEqual(joined[0].text, "1. 사업개요")


class RowFragmentTests(unittest.TestCase):
    def test_justified_words_join_into_one_line(self):
        parts = [line("고용노동부장관이", (63, 622, 163, 637), 15), line("고시하는", (174, 622, 225, 637), 15),
                 line("최저임금액(시급", (236, 622, 327, 637), 15)]
        joined = P.group_rows(parts, P.Stats())
        self.assertEqual([j.text for j in joined], ["고용노동부장관이 고시하는 최저임금액(시급"])
        self.assertIn("row_fragments_joined", joined[0].transformations)
        self.assertEqual(joined[0].source_text, "고용노동부장관이 고시하는 최저임금액(시급")

    def test_cells_divided_by_vertical_rule_stay_apart(self):
        parts = [line("물수건", (100, 50, 130, 60)), line("신문", (138, 50, 158, 60))]
        self.assertEqual(len(P.group_rows(parts, P.Stats(), [(134, 45, 65)])), 2)
        self.assertEqual(len(P.group_rows(parts, P.Stats(), [(134, 55, 65)])), 1)

    def test_numbers_arrows_and_wide_gaps_stay_apart(self):
        for a, b in (("10", "11"), ("교육 개발", "▶")):
            with self.subTest(pair=(a, b)):
                parts = [line(a, (100, 50, 120, 60)), line(b, (125, 50, 145, 60))]
                self.assertEqual(len(P.group_rows(parts, P.Stats())), 2)
        wide = [line("왼쪽 칸", (100, 50, 130, 60)), line("오른쪽 칸", (150, 50, 190, 60))]
        self.assertEqual(len(P.group_rows(wide, P.Stats())), 2)

    def test_vertical_rules_ignore_filled_glyph_strokes(self):
        class Point:
            def __init__(self, x, y):
                self.x, self.y = x, y

        stroke = {"type": "s", "items": [("l", Point(10, 0), Point(10, 20))]}
        glyph = {"type": "f", "items": [("l", Point(30, 0), Point(30, 20))]}
        self.assertEqual(P.vertical_rules([stroke, glyph]), [(10, 0, 20)])


@requires_corpus
class SameRowSourceTests(unittest.TestCase):
    @staticmethod
    @lru_cache
    def parse(identity):
        record = next(r for r in source_records() if r["document_id"] == identity)
        return P.parse_pdf(Path(record["pdf_path"]))[0]

    def test_issue_overview_chapters_become_sections(self):
        for identity, head in (("doc_ef6d9383c6ff0690", "추진개요"), ("doc_80f858af687f09cb", "사업개요"),
                               ("doc_8f8152f2e456ee9f", "사업 개요"), ("doc_9d949f66ef7c0e47", "교통대책 개요")):
            with self.subTest(identity=identity):
                doc = self.parse(identity)
                self.assertTrue(any(s["head_text"] == head and s["level"] == 1 and s["marker"] in "ⅠⅡ"
                                    for s in doc["sections"]))
                self.assertFalse([b for b in doc["blocks"] if b["kind"] == "para" and b["text"] in ("Ⅰ", "Ⅱ")])

    def test_first_word_of_line_is_not_moved_to_end(self):
        doc = self.parse("doc_c0d2268528dcf158")
        self.assertTrue(any(b["text"].startswith("우리구 관내『상도4동 안전한 통학로") for b in doc["blocks"]))

    def test_label_precedes_count_on_same_row(self):
        doc = self.parse("doc_7b05f2d19f16f02b")
        texts = [b["text"] for b in doc["blocks"] if b["page"] == 1]
        self.assertTrue(texts[texts.index("7 개") - 1].startswith("정책환경 변화로"))

    def test_justified_line_is_one_block(self):
        doc = self.parse("doc_f302215a42767bd8")
        self.assertTrue(any("고용노동부장관이 고시하는 최저임금액(시급 기준." in b["text"] for b in doc["blocks"]))

    def test_reading_order_contract_on_issue_documents(self):
        for identity in ("doc_8f8152f2e456ee9f", "doc_9d949f66ef7c0e47", "doc_c0d2268528dcf158",
                         "doc_7b05f2d19f16f02b", "doc_f302215a42767bd8"):
            with self.subTest(identity=identity):
                report = checks.Report()
                checks.check_reading_order(self.parse(identity), identity, report)
                self.assertFalse(report.violations)


if __name__ == "__main__":
    unittest.main()
