"""#26 줄 분류·정규화: 제어 문자, 글머리 기호, 날짜, 표 안 장 제목, 제목 조각, 장 섹션."""

import unittest
from functools import lru_cache
from pathlib import Path

from parser_checks import requires_corpus, source_records

from bareum_ai.dataset.parsing import parser as P


def raw(text, x=0, y=0, size=10):
    chars = [{"c": c, "bbox": (x + i * 5, y, x + (i + 1) * 5, y + size)} for i, c in enumerate(text)]
    return {"bbox": (x, y, x + len(text) * 5, y + size), "spans": [{"size": size, "color": 0, "chars": chars}]}


def classify(text):
    return P.classify(P.build_line(raw(text), P.Stats()), 0, 1)


class ControlCharTests(unittest.TestCase):
    def test_control_chars_become_spaces_but_stay_in_source(self):
        line = P.build_line(raw("\x01 \x01 -\x01 전년도\x01 대비"), P.Stats())
        self.assertEqual(line.text, "- 전년도 대비")
        self.assertIn("\x01", line.source_text)
        self.assertEqual([w.text for w in line.words], ["-", "전년도", "대비"])
        block = P.classify(line, 0, 1)
        self.assertEqual((block.kind, block.marker), ("item", "-"))


class MarkerTests(unittest.TestCase):
    def test_variant_markers_follow_manual_levels(self):
        for marker, level in (("❐", 3), ("❏", 3), ("☐", 3), ("∎", 3), ("∘", 4), ("⦁", 4), ("･", 6), ("・", 6)):
            with self.subTest(marker=marker):
                block = classify(f"{marker} 협약 주요내용")
                self.assertEqual((block.kind, block.level, block.marker_raw), ("item", level, marker))

    def test_decorative_markers_are_items_without_level(self):
        for marker in "◈▸▹▻►✓➠❙§¦":
            with self.subTest(marker=marker):
                block = classify(f"{marker} 점검 총괄 : 민원순찰팀장")
                self.assertEqual((block.kind, block.level, block.marker_type), ("item", None, "shape"))

    def test_marker_line_starts_new_block(self):
        lines = [P.build_line(raw("❐ 치료중심, 시설 중심의 보건의료체계로 건강 불평등 심화", 0, 0), P.Stats()),
                 P.build_line(raw("❐ 지역사회 돌봄요구 증가에 따른 선도사업의 일환", 0, 12), P.Stats())]
        self.assertEqual(len(P.merge_wrapped(lines, P.Stats())), 2)


class DateTests(unittest.TestCase):
    def test_month_day_is_not_section_number(self):
        for text in ("2. 14(수) 바이애슬론 경기 관람", "5. 30~31.(화)", "3. 21.(목) 환경위원회"):
            with self.subTest(text=text):
                self.assertNotEqual(classify(text).kind, "heading")

    def test_numbered_heading_still_detected(self):
        for text in ("2. 추진배경", "1. 3대 추진과제", "10. 2020년 추진계획"):
            with self.subTest(text=text):
                self.assertEqual(classify(text).kind, "heading")


class TableHeadingTests(unittest.TestCase):
    def test_two_row_title_box(self):
        self.assertEqual(P.heading_from_grid([["Ⅲ", ""], ["", "평가개요"]]), (1, "Ⅲ", "평가개요"))
        self.assertIsNone(P.heading_from_grid([["", "평가개요"], ["Ⅲ", ""]]))
        self.assertIsNone(P.heading_from_grid([["Ⅲ", "개요"], ["비고", ""]]))

    def test_roman_and_large_type_confirm_without_label_word(self):
        self.assertEqual(P.table_heading_hint([["Ⅳ", "행정사항"]], True)["status"], "confirmed")
        self.assertEqual(P.table_heading_hint([["Ⅳ", "행정사항"]], False)["status"], "needs_review")
        self.assertEqual(P.table_heading_hint([["1", "동작구민 단체관람 지원"]], True)["status"], "needs_review")

    def test_number_only_box_beside_title(self):
        title = P.Line("행정사항", "행정사항", (108, 682, 180, 696), 14, [P.Word("행정사항", (108, 682, 180, 696))])
        self.assertTrue(P.chapter_number_box([["Ⅲ", ""]], (53, 682, 63, 694), [title]))
        far = P.Line("행정사항", "행정사항", (300, 682, 372, 696), 14, [P.Word("행정사항", (300, 682, 372, 696))])
        self.assertFalse(P.chapter_number_box([["Ⅲ", ""]], (53, 682, 63, 694), [far]))
        self.assertFalse(P.chapter_number_box([["3", ""]], (53, 682, 63, 694), [title]))


class TitleFragmentTests(unittest.TestCase):
    def test_overlapping_title_copies_are_marked(self):
        def block(text, box, size=20):
            return P.Block("para", text, text, 1, box, size=size)

        body = [block(f"본문 {i}", (60, 300 + i * 20, 500, 312 + i * 20), 12) for i in range(6)]
        title = block("2021년 언론수용자 조사 실시 계획(안)", (135, 70, 469, 90))
        copies = [block("년 언론수용자", (180, 70, 306, 92)), block("계획((안", (403, 70, 464, 92))]
        other = block("미디어연구센터", (200, 70, 300, 90))
        blocks = [title, *copies, other, *body]
        P.mark_document_title(blocks, 842)
        self.assertEqual(title.kind, "document_title")
        self.assertEqual([b.kind for b in copies], ["title_fragment", "title_fragment"])
        self.assertEqual(other.kind, "para")


class ChapterSectionTests(unittest.TestCase):
    def sections(self, head):
        blocks = [head, P.Block("item", "□ 내용", "내용", 1, (0, 20, 10, 30), level=3)]
        P.assign_hierarchy(blocks, "doc")
        return P.build_sections(blocks, "doc")

    def test_attachment_chapter_is_section(self):
        head = P.Block("heading", "Ⅵ 붙임", "붙임", 1, (0, 0, 10, 10), level=1, marker="Ⅵ", marker_type="roman")
        self.assertEqual([s.head_text for s in self.sections(head)], ["붙임"])

    def test_roman_chapter_note_does_not_count_toward_length(self):
        text = "동작휴양소 운영 상 문제점 (운영체계 개선배경)"
        head = P.Block("heading", text, text, 1, (0, 0, 10, 10), level=1, marker="Ⅱ", marker_type="roman")
        self.assertEqual(len(self.sections(head)), 1)
        item = P.Block("item", "소요예산: 금XX,XXX,XXX원(금XXXXXX원)", "소요예산: 금XX,XXX,XXX원(금XXXXXX원)",
                       1, (0, 0, 10, 10), level=3, marker="□", marker_type="square")
        self.assertEqual(self.sections(item), [])


@requires_corpus
class ClassificationSourceTests(unittest.TestCase):
    @staticmethod
    @lru_cache
    def parse(identity):
        record = next(r for r in source_records() if r["document_id"] == identity)
        return P.parse_pdf(Path(record["pdf_path"]))[0]

    def heads(self, identity):
        return {s["head_text"]: s for s in self.parse(identity)["sections"]}

    def test_table_and_box_chapters_become_sections(self):
        for identity, head in (("doc_9d949f66ef7c0e47", "행정사항"), ("doc_65e3fac092a65609", "자원봉사 인프라"),
                               ("doc_2a83f68406cc32da", "평가개요"), ("doc_ebaf5c63866d198d", "행정사항"),
                               ("doc_ebaf5c63866d198d", "동작휴양소 운영 상 문제점 (운영체계 개선배경)")):
            with self.subTest(identity=identity, head=head):
                self.assertEqual(self.heads(identity)[head]["level"], 1)

    def test_overlapping_title_copies_leave_body(self):
        doc = self.parse("doc_80f858af687f09cb")
        page1 = [b for b in doc["blocks"] if b["page"] == 1 and b["bbox"][1] < 95]
        self.assertFalse([b["text"] for b in page1 if b["kind"] == "para"])

    def test_control_chars_and_dates(self):
        doc = self.parse("doc_457f532591e629fd")
        self.assertFalse([b for b in doc["blocks"] if "\x01" in b["text"]])
        self.assertTrue(any(b["kind"] == "item" and b["text"].startswith("전년도 동기간") for b in doc["blocks"]))
        doc = self.parse("doc_2e732480628ec321")
        self.assertFalse([b for b in doc["blocks"] if b["kind"] == "heading" and b["text"].startswith("14(수)")])

    def test_ballot_box_marker_is_square_item(self):
        doc = self.parse("doc_9fa56a7b75b7bcaa")
        block = next(b for b in doc["blocks"] if b["text"] == "협약 주요내용")
        self.assertEqual((block["kind"], block["level"], block["marker_raw"]), ("item", 3, "☐"))


if __name__ == "__main__":
    unittest.main()
