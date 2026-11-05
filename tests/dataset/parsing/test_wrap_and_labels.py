"""#26 원본 점검에서 나온 줄 병합·항목 이름·붙임·표어·단위 표시 오류."""

import unittest
from dataclasses import replace
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


    def test_inline_figure_just_below_line_does_not_block(self):
        # 줄 안 그림이 앞 줄 아래로 1.5pt 삐져나온다('교구' 뒤 가운뎃점 그림).
        first = line("○ (홍보) 과학관에서 직접 기획하고 개발 제작한 전시물, 도서, 교구", 57, 697, 538, 18)
        rest = line("등을 소개하여, 바이럴 마케팅 홍보", 85, 723, 325, 18)
        self.assertEqual(len(P.merge_wrapped([first, rest], P.Stats(), [(352, 697, 369, 716.5)])), 1)


class MergeBoundaryTests(unittest.TestCase):
    def test_boxed_text_merges_on_shared_right_edge(self):
        # 상자 안 줄은 페이지 끝(560)보다 안쪽(523.6)에서 같이 끝난다.
        lines = [
            line("○ 생활예술인 및 전문예술인들에게 다양한 문화예술 창출의 기회를 제공", 70, 140, 523.6, 14),
            line("하고, 도민에게는 일상에서 다양한 문화향유의 기회를 제공", 90, 159, 420, 14),
            line("○ 다양한 세대, 장르, 장소를 넘어 모두가 함께 평등한 기회를 제공하", 70, 178, 523.6, 14),
            line("였으며, 이로 인해 지역예술인 육성에 기여", 90, 197, 400, 14),
            line("○ 경기아트센터가 31개 시군에서 실시하던 본 사업을 실시함으로써 경기", 70, 216, 523.6, 14),
            line("도 지역예술인들에게 더 나은 무대와 경험을 제공함", 90, 235, 430, 14),
            line("□ 사업 개요는 다음과 같으며 세부 일정은 붙임과 같이 운영할 계획입니다", 60, 280, 560, 14),
        ]
        self.assertEqual(len(P.merge_wrapped(lines, P.Stats())), 4)
        lines[0] = line("○ 생활예술인 및 전문예술인들에게 다양한 문화예술 창출의 기회 제공(예정)", 70, 140, 523.6, 14)
        self.assertEqual(len(P.merge_wrapped(lines, P.Stats())), 5)

    def test_box_border_is_line_end(self):
        # 상자 테두리(x=548)가 줄 끝이다. 페이지 끝(562)까지는 한 글자 넘게 모자란다.
        lines = [
            line("우리구 관내『상도4동 안전한 통학로 조성사업』과 관련하여", 58.9, 163.7, 540, 15),
            line("토지소유자에 대한 토지매도 계약 가능여부 등 협의 결과를 아래와", 43.9, 187.7, 539.9, 15),
            line("같이 보고합니다.", 43.9, 211.7, 167.8, 15),
            line("○ 목적 : 상도초등학교 학생들의 주 통학로로 이용되고 있는 상도동", 34, 285.4, 561.2, 15),
        ]
        border = [(548.5, 157, 238)]
        self.assertEqual(len(P.merge_wrapped(lines, P.Stats())), 4)
        self.assertEqual(len(P.merge_wrapped(lines, P.Stats(), rules=border)), 2)
        # 좁은 표 칸 글은 칸 세로선에 닿아도 잇지 않는다.
        cells = [line("13:00~13:05", 60, 100, 118, 15), line("13:10~13:30", 60, 120, 118, 15), lines[3]]
        self.assertEqual(len(P.merge_wrapped(cells, P.Stats(), rules=[(120, 90, 160)])), 3)

    def test_image_bullet_starts_new_item(self):
        first = line("- 안전신문고 앱 다운로드 및 사용방법 안내로 시민 안전신고 활성화", 63.8, 261, 549.7, 15)
        head = line("홍보 방법", 67, 291.7, 132.8, 15)
        self.assertEqual(len(P.merge_wrapped([first, head], P.Stats(), [(41.1, 290.0, 59.6, 306.7)])), 2)
        self.assertEqual(len(P.merge_wrapped([first, head], P.Stats())), 1)

    def test_bold_signature_does_not_merge_with_date(self):
        date = line("2018. 12. 28.", 446.8, 275, 552.8, 15)
        sign = replace(line("성남시 수정구청장", 417.7, 299, 552.7, 15), is_bold=True)
        self.assertEqual(len(P.merge_wrapped([date, sign], P.Stats())), 2)

    def test_label_line_after_label_line_is_new_entry(self):
        first = line("기간 : 2018. 7. 28.(토) ~ 8. 11.(토) [기간 중 13일]", 93, 382.8, 532, 15)
        place = line("장소 : 동작 주차공원", 93, 404.2, 272.9, 15)
        self.assertEqual(len(P.merge_wrapped([first, place], P.Stats())), 2)

    def test_soft_hyphen_is_dash_marker(self):
        block = P.classify(line("\xad 특히, 민선7기를 맞이하여 그동안 추진중인 사업을", 60, 100, 500), 50, 1)
        self.assertEqual((block.kind, block.marker), ("item", "\xad"))


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

    def test_marker_only_attachment_takes_title_below(self):
        heads = [s["head_text"] for s in self.parse("doc_4dd4862e76664f75")["sections"] if s["marker"] == "붙 임 1"]
        self.assertEqual(heads, ["2019년 산불감시원 및 산불전문진화대원 모집 공고"])

    def test_boxed_summary_lines_merge(self):
        blocks = self.parse("doc_43a0776d96eb0d2f")["blocks"]
        self.assertTrue([b for b in blocks if b["text"].startswith("경기도 생활예술인") and b["text"].endswith("문화향유의 기회’를 제공")])

    def test_boxed_intro_paragraph_merges(self):
        blocks = self.parse("doc_c0d2268528dcf158")["blocks"]
        self.assertTrue([b for b in blocks if b["text"].startswith("우리구 관내") and b["text"].endswith("같이 보고합니다.")])

    def test_image_bullet_heading_stays_apart(self):
        blocks = self.parse("doc_e07c4af3ba527c82")["blocks"]
        self.assertTrue([b for b in blocks if b["text"].strip() == "홍보 방법"])

    def test_image_bullet_pages_are_flagged(self):
        doc = self.parse("doc_e07c4af3ba527c82")
        flagged = {w["page"] for w in doc["warnings"] if w["code"] == "marker_shape_unverified"}
        self.assertIn(7, flagged)
        self.assertTrue(doc["pages"][6]["requires_vision"])
        # 기관명 앞 로고는 글머리가 아니다.
        logo = self.parse("doc_d6e3d8550b942aba")["warnings"]
        self.assertFalse([w for w in logo if w["code"] == "marker_shape_unverified"])

    def test_dashed_slogan_keeps_title_intact(self):
        doc = self.parse("doc_61cecbb5ae10d34c")
        title = next(b for b in doc["blocks"] if b["kind"] == "document_title")
        self.assertEqual(title["text"], "2020년 온라인 태교 교실 결과보고")
        self.assertFalse([s for s in doc["sections"] if "문산보건지소" in s["head_text"]])


if __name__ == "__main__":
    unittest.main()
