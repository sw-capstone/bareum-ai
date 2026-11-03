"""#26 원본 점검에서 나온 줄 병합·항목 이름·붙임·표어·단위 표시 오류."""

import unittest
from functools import lru_cache
from pathlib import Path

from parser_checks import requires_corpus, source_records

from bareum_ai.dataset.parsing import parser as P


def line(text, x0, y, x1, size=13):
    # 어절마다 글자 수에 비례한 좌표를 준다.
    width = (x1 - x0) / max(len(text), 1)
    words, start = [], 0
    for token in text.split():
        start = text.index(token, start)
        left = x0 + width * start
        words.append(P.Word(token, (left, y, left + width * len(token), y + size), start, start + len(token)))
        start += len(token)
    return P.Line(text, text, (x0, y, x1, y + size), size, words)


class HangingIndentTests(unittest.TestCase):
    def test_continuation_aligned_to_label_value_merges(self):
        first = line("❍ 필요성 : 고질적인 자동차세 체납자 차량이 다수 존재하며 중복 단속지역을", 51.6, 421.7, 539.4)
        value = first.words[3].bbox[0]
        rest = line("줄이고 단속제외 지역 판별이 요구됨", value, 442.9, 360.0)
        self.assertEqual(len(P.merge_wrapped([first, rest], P.Stats())), 1)

    def test_line_ending_within_one_em_still_wraps(self):
        # 다음 어절이 들어가지 않아 오른쪽 끝에서 10pt 모자라게 끝난 줄.
        first = line("○ 일부 참여자는 대면 프로그램 재개를 희망하고, 쉬운 동영상 설명 및", 48.2, 598.1, 552.9)
        edge = line("○ 다른 항목이 오른쪽 끝까지 찬다", 48.2, 640.0, 563.6)
        rest = line("필요함", 61.2, 619.2, 100.0)
        self.assertEqual(len(P.merge_wrapped([first, rest, edge], P.Stats())), 2)

    def test_inline_figure_does_not_block_wrap(self):
        # 실제 줄 높이(18pt)로 둔다. 그림은 첫 줄 아래로 0.7pt 삐져나온다.
        first = line("○ 대한출판문화협회의 2022 서울국제도서전 참가 요청에 따라", 64.1, 290.3, 546.1, 18)
        rest = line("생활 속에 숨어있는 과학원리를 재미있게 체험할 수 있는 과학", 87.2, 316.4, 538.6, 18)
        inline = (239.2, 290.0, 256.9, 309.0)  # 줄 아래로 0.7pt 삐져나온 줄 안 그림
        self.assertEqual(len(P.merge_wrapped([first, rest], P.Stats(), [inline])), 1)
        between = (100, 309.5, 300, 316.0)
        self.assertEqual(len(P.merge_wrapped([first, rest], P.Stats(), [between])), 2)


class RowLabelTests(unittest.TestCase):
    def test_spaced_label_joins_without_space(self):
        parts = [line("○ 일", 60, 100, 85), line("자 : 2019. 4. 15. ~ 4. 16.", 115, 100, 300)]
        self.assertEqual([j.text for j in P.group_rows(parts, P.Stats())], ["○ 일자 : 2019. 4. 15. ~ 4. 16."])

    def test_wide_gap_between_ordinary_words_stays_apart(self):
        parts = [line("○ 사업 추진", 60, 100, 130), line("결과 보고", 170, 100, 220)]
        self.assertEqual(len(P.group_rows(parts, P.Stats())), 2)

    def test_touching_bracket_joins_its_text(self):
        parts = [line("｢", 67.9, 152.6, 75.4, 15), line("책을 탈출한 과학｣ 체험존을", 74.8, 152.6, 300, 15)]
        self.assertEqual([j.text for j in P.group_rows(parts, P.Stats())], ["｢책을 탈출한 과학｣ 체험존을"])

    def test_attachment_label_joins_title_on_same_row(self):
        parts = [line("붙임 2", 70, 80, 119, 15), line("국립과천과학관 체험부스", 143, 80, 392, 15)]
        joined = P.group_rows(parts, P.Stats())
        block = P.classify(joined[0], 50, 1)
        self.assertEqual((block.kind, block.marker, block.text), ("attachment", "붙임 2", "국립과천과학관 체험부스"))


class DashedLabelTests(unittest.TestCase):
    def test_dashed_slogan_is_not_dash_item(self):
        for text in ("- 문산보건지소 -", "-장애인이 잘사는 통합복지 추진을 위한-"):
            with self.subTest(text=text):
                self.assertEqual(P.classify(line(text, 200, 100, 380), 50, 1).kind, "para")
        self.assertEqual(P.classify(line("- 추진기간 : 2019. 2. ~ 5.", 70, 100, 380), 50, 1).kind, "item")

    def test_dashed_slogan_does_not_merge_with_title(self):
        slogan = line("- 사회적 거리두기 1.5단계 격상에 따른 -", 150, 100, 420)
        title = line("체육시설 운영제한 및 관리계획", 160, 118, 410)
        self.assertEqual(len(P.merge_wrapped([slogan, title], P.Stats())), 2)


@requires_corpus
class ReviewSourceTests(unittest.TestCase):
    @staticmethod
    @lru_cache
    def parse(identity):
        record = next(r for r in source_records() if r["document_id"] == identity)
        return P.parse_pdf(Path(record["pdf_path"]))[0]

    def test_no_one_character_sections(self):
        for identity in ("doc_82782c97c0345c98", "doc_0a8dde9487840002"):
            with self.subTest(identity=identity):
                heads = [s["head_text"] for s in self.parse(identity)["sections"]]
                self.assertFalse([h for h in heads if len(h.replace(" ", "")) <= 1])

    def test_label_value_continuations_merge(self):
        blocks = self.parse("doc_d42d3c50855ac9eb")["blocks"]
        need = next(b for b in blocks if b["text"].startswith("필요성 :"))
        self.assertTrue(need["text"].endswith("판별이 요구됨"))
        self.assertFalse([b for b in blocks if b["kind"] == "item" and b["text"].endswith("주관 -")])

    def test_unit_caption_stays_out_of_table(self):
        blocks = self.parse("doc_0e093e0ee42d2a04")["blocks"]
        tables = [b for b in blocks if b["kind"] == "table" and b["page"] == 2]
        self.assertEqual(tables[0]["rows"][0][:2], ["구분", "건명"])
        self.assertTrue([b for b in blocks if b["kind"] == "para" and b["text"] == "(단위 : 원)"])

    def test_dashed_slogan_keeps_title_intact(self):
        doc = self.parse("doc_61cecbb5ae10d34c")
        title = next(b for b in doc["blocks"] if b["kind"] == "document_title")
        self.assertEqual(title["text"], "2020년 온라인 태교 교실 결과보고")
        self.assertFalse([s for s in doc["sections"] if "문산보건지소" in s["head_text"]])


if __name__ == "__main__":
    unittest.main()
