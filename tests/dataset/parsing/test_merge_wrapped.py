"""#26 줄 병합: 앞쪽 공백 때문에 놓친 병합, 누적 bbox 때문에 생긴 과병합."""

import unittest
from functools import lru_cache
from pathlib import Path

from parser_checks import requires_corpus, source_records

from bareum_ai.dataset.parsing import parser as P


def line(text, x0, y, x1, text_x0=None, size=15):
    # 줄 박스는 앞쪽 공백을 포함한다. 실제 글자 시작(text_x0)은 첫 어절 좌표에 둔다.
    start = text_x0 if text_x0 is not None else x0
    words = [P.Word(text.split()[0], (start, y, start + size, y + size))]
    return P.Line(text, text.strip(), (x0, y, x1, y + size), size, words, trailing_space=True)


class LeadingSpaceTests(unittest.TestCase):
    def test_continuation_merges_by_actual_text_start(self):
        # 줄 박스 차이 31pt(허용치 30pt 초과), 실제 글자 시작 차이 24pt.
        lines = [line(" ◈ 새만금 비점오염저감대책 시행으로 김제시는 비점오염 저감계획에", 70.6, 208.7, 529.3, 78.0),
                 line("따른 비점오염저감시설(인공습지) 조성사업을 추진 중으로,", 101.9, 234.3, 446.0)]
        merged = P.merge_wrapped(lines, P.Stats())
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].line_count, 2)

    def test_sentence_end_keeps_relaxed_lines_apart(self):
        lines = [line(" ◈ 물관리 일원화에 따라 K-water 역할을 수행하고자 함", 70.6, 208.7, 529.3, 78.0),
                 line("따른 비점오염저감시설(인공습지) 조성사업을 추진 중으로,", 101.9, 234.3, 446.0)]
        self.assertEqual(len(P.merge_wrapped(lines, P.Stats())), 2)


class ShortFinalLineTests(unittest.TestCase):
    def test_short_last_line_ends_paragraph(self):
        lines = [line("한국수자원공사는 전국의 댐, 상수도 등 수자원의 종합적 관리를 통해 국", 60, 100, 548.6),
                 line("민 삶의 질 향상과 국가 발전에 기여하는 물 전문 공기업 입니다.", 60, 118, 386.3),
                 line("지금부터는 귀하께서 경험하신 수탁운영사업에 대해 질문 드리겠습니다.", 60, 136, 548.6)]
        merged = P.merge_wrapped(lines, P.Stats())
        self.assertEqual([m.line_count for m in merged], [2, 1])


@requires_corpus
class MergeSourceTests(unittest.TestCase):
    @staticmethod
    @lru_cache
    def parse(identity):
        record = next(r for r in source_records() if r["document_id"] == identity)
        return P.parse_pdf(Path(record["pdf_path"]))[0]

    def test_wetland_sentence_is_parent_and_child_item(self):
        blocks = self.parse("doc_8f8152f2e456ee9f")["blocks"]
        parent = next(b for b in blocks if "김제시는" in b["text"])
        child = next(b for b in blocks if "K-water 기술력" in b["text"])
        self.assertTrue(parent["text"].endswith("추진 중으로,"))
        self.assertTrue(child["text"].endswith("사업 참여 적극 희망"))
        self.assertEqual((parent["marker"], child["marker"]), ("◈", "◦"))

    def test_short_final_line_ends_previous_paragraph(self):
        blocks = self.parse("doc_31668196b750198b")["blocks"]
        intro = next(b for b in blocks if b["page"] == 15 and b["text"].startswith("한국수자원공사는 전국의"))
        self.assertNotIn("지금부터는", intro["text"])


if __name__ == "__main__":
    unittest.main()
