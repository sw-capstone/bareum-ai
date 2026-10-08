"""재현된 파서 결함의 회귀 검사."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import parser_checks as checks
import pymupdf

from bareum_ai.dataset.parsing import parser as P


def line(text, box=(10, 10, 90, 20), trailing=False, size=10):
    # 부분 셀 투영을 검증할 수 있도록 실제 원문 위치를 가진 어절을 만든다.
    words = []
    for m in re.finditer(r"\S+", text):
        width = (box[2] - box[0]) / max(len(text), 1)
        bbox = (box[0] + width * m.start(), box[1], box[0] + width * m.end(), box[3])
        words.append(P.Word(m.group(), bbox, m.start(), m.end()))
    return P.Line(text, text.strip(), box, size, words, trailing_space=trailing)


def raw_line(text, x=0, y=0, color=0):
    chars = [
        {"c": c, "bbox": (x + i * 5, y, x + (i + 1) * 5, y + 10)}
        for i, c in enumerate(text)
    ]
    return {
        "bbox": (x, y, x + len(text) * 5, y + 10),
        "spans": [{"size": 10, "color": color, "chars": chars}],
    }


class TextPage:
    def __init__(self, *lines):
        self.lines = lines

    def get_text(self, _):
        return {"blocks": [{"type": 0, "lines": self.lines}]}


class TablePage:
    number = 0
    rect = pymupdf.Rect(0, 0, 595, 841)
    derotation_matrix = pymupdf.Identity

    def __init__(self, rows, bbox=(0, 0, 100, 100)):
        self.table = NS(bbox=bbox, rows=[NS(cells=r) for r in rows])

    def find_tables(self, **kwargs):
        return NS(tables=[self.table])

    def get_drawings(self):
        return []


class LineRegressionTests(unittest.TestCase):
    def test_overlap_does_not_delete_different_text(self):
        stats = P.Stats()
        lines = P.extract_lines(TextPage(raw_line("ABC"), raw_line("XYZ")), stats)
        self.assertEqual([entry.text for entry in lines], ["ABC", "XYZ"])
        self.assertEqual(stats.dropped_duplicates, 0)

    def test_fake_bold_still_deduplicates_and_prefers_dark(self):
        stats = P.Stats()
        lines = P.extract_lines(
            TextPage(raw_line("ABC", color=0xCCCCCC), raw_line("ABC", x=0.1)), stats
        )
        self.assertEqual(len(lines), 1)
        self.assertFalse(lines[0].is_light)
        self.assertEqual(stats.dropped_duplicates, 1)

    def test_shadow_in_other_column_is_not_deleted(self):
        light = line("ABC", (0, 0, 20, 10))
        light.is_light = True
        dark = line("ABCDEF", (100, 0, 160, 10))
        self.assertEqual(len(P.drop_shadows([light, dark], P.Stats())), 2)

    def test_two_columns_never_merge_across_columns(self):
        lines = [
            line(s, b)
            for s, b in [
                ("LEFT1", (0, 100, 90, 110)),
                ("RIGHT1", (120, 100, 200, 110)),
                ("LEFT2", (0, 112, 90, 122)),
                ("RIGHT2", (120, 112, 200, 122)),
            ]
        ]
        result = P.merge_wrapped(P.group_rows(lines, P.Stats()), P.Stats())
        self.assertEqual(
            [entry.text for entry in result], [entry.text for entry in lines]
        )

    def test_same_row_never_wraps(self):
        self.assertEqual(
            len(
                P.merge_wrapped(
                    [
                        line("RIGHT", (300, 100, 500, 110)),
                        line("LEFT", (50, 100, 200, 110)),
                    ],
                    P.Stats(),
                )
            ),
            2,
        )

    def test_real_wrap_preserves_word_boundary(self):
        lines = [line("alpha ", trailing=True), line("beta", (10, 22, 90, 32))]
        result = P.merge_wrapped(lines, P.Stats())
        self.assertEqual(result[0].text, "alpha beta")
        self.assertEqual(result[0].source_text, "alpha \nbeta")
        self.assertEqual(lines[0].text, "alpha")
        for w in result[0].words:
            self.assertEqual(
                result[0].source_text[w.source_start : w.source_end], w.text
            )

    def test_word_broken_across_lines_still_joins(self):
        result = P.merge_wrapped(
            [line("alpha"), line("bet", (10, 22, 90, 32))], P.Stats()
        )
        self.assertEqual(result[0].text, "alphabet")

    def test_table_barrier_prevents_wrap(self):
        lines = [line("alpha"), line("beta", (10, 28, 90, 38))]
        self.assertEqual(len(P.merge_wrapped(lines, P.Stats(), [(0, 21, 100, 27)])), 2)

    def test_attachment_starts_new_block(self):
        result = P.merge_wrapped(
            [line("본문"), line("붙임 1 목록", (10, 22, 90, 32))], P.Stats()
        )
        self.assertEqual(len(result), 2)

    def test_attachment_word_boundary(self):
        for text in (
            "참고사항을 확인한다",
            "첨부파일을 제출한다",
            "별첨자료를 확인한다",
        ):
            with self.subTest(text=text):
                self.assertEqual(P.classify(line(text), 10, 1).kind, "para")
        for text in ("붙임 1 목록", "참고", "첨부: 자료", "붙 임 2. 목록"):
            with self.subTest(text=text):
                self.assertEqual(P.classify(line(text), 10, 1).kind, "attachment")

    def test_number_marker_keeps_author_punctuation(self):
        original = line("1", (0, 0, 5, 10))
        joined = P.group_rows([original, line("사업개요", (10, 0, 50, 10))], P.Stats())[
            0
        ]
        block = P.classify(joined, 0, 1)
        self.assertEqual(
            (block.marker, block.marker_raw, block.marker_normalized), ("1", "1", "1.")
        )
        self.assertEqual(original.text, "1")

    def test_distant_single_letters_are_not_a_title(self):
        self.assertEqual(
            len(
                P.group_rows(
                    [line("A", (0, 0, 5, 10)), line("B", (100, 0, 105, 10))], P.Stats()
                )
            ),
            2,
        )

    def test_running_head_uses_ceiling(self):
        pages = [[line("반복", (0, 0, 40, 10))] for _ in range(2)] + [[]] * 3
        self.assertFalse(P.detect_running_elements(pages, [1000] * 5, P.Stats()))


class TableRegressionTests(unittest.TestCase):
    def test_cell_line_wrap_keeps_explicit_space(self):
        page = TablePage([[(0, 0, 100, 100)]])
        blocks, remaining = P.extract_tables(
            page,
            [line("alpha ", trailing=True), line("beta", (10, 22, 90, 32))],
            P.Stats(),
            [],
        )
        self.assertEqual(blocks[0].rows, [["alpha beta"]])
        self.assertEqual(blocks[0].source_rows, [["alpha \nbeta"]])
        self.assertFalse(remaining)

    def cell_rows(self, cell, lines):
        blocks, _ = P.extract_tables(TablePage([[cell]], cell), lines, P.Stats(), [])
        return blocks[0].rows

    def test_line_break_with_room_left_keeps_space(self):
        # 첫 줄이 가장 길어도 셀 끝에 자리가 남았으면 작성자가 직접 바꾼 줄이다.
        rows = self.cell_rows(
            (0, 0, 200, 40),
            [line("10월 초순", (5, 5, 60, 15)), line("10월 내", (5, 20, 45, 30))],
        )
        self.assertEqual(rows, [["10월 초순 10월 내"]])

    def test_centered_date_lines_keep_space_and_source(self):
        rows = self.cell_rows(
            (100, 80, 300, 140),
            [line("10월 초순", (175, 95, 225, 105)),
             line("10월 내", (180, 115, 220, 125))],
        )
        self.assertEqual(rows, [["10월 초순 10월 내"]])

    def test_independent_values_keep_boundaries_across_cell_layouts(self):
        # 실제 삽입 글자의 폭을 측정한다. Font("korea")의 폭과 insert_text 폭은 다르다.
        pairs = [("10월 초순", "10월 내"), ("2021. 10. 1.", "2021. 10. 2."),
                 ("2021-10-01", "2021-10-02"), ("오전 9시", "오후 2시"),
                 ("1,000원", "2,000원"), ("홍보협력과", "전시연구단")]
        with tempfile.TemporaryDirectory() as directory:
            for index, (texts, align, margin, size) in enumerate(
                (texts, align, margin, size) for texts in pairs
                for align in ("left", "center", "right") for margin in (10, 100)
                for size in (8, 12)
            ):
                with self.subTest(texts=texts, align=align, margin=margin, size=size):
                    measure = pymupdf.open()
                    page = measure.new_page()
                    for text, y in zip(texts, (30, 70)):
                        page.insert_text((10, y), text, fontname="korea", fontsize=size)
                    lines = [l for b in page.get_text("dict")["blocks"] if b["type"] == 0 for l in b["lines"]]
                    lengths = [l["bbox"][2] - l["bbox"][0] for l in lines]
                    measure.close()
                    right = 100 + max(lengths) + margin
                    path = Path(directory) / f"case_{index}.pdf"
                    pdf = pymupdf.open()
                    page = pdf.new_page()
                    page.draw_rect((50, 80, right, 150))
                    page.draw_line((100, 80), (100, 150))
                    page.insert_text((55, 105), "기간", fontname="korea", fontsize=10)
                    for text, length, y in zip(texts, lengths, (105, 105 + size * 2)):
                        x = (105 if align == "left" else right - 5 - length if align == "right"
                             else (100 + right - length) / 2)
                        page.insert_text((x, y), text, fontname="korea", fontsize=size)
                    pdf.save(path)
                    pdf.close()
                    doc, markdown, _ = P.parse_pdf(path)
                    table = next(b for b in doc["blocks"] if b["kind"] == "table")
                    self.assertEqual(table["rows"], [["기간", " ".join(texts)]])
                    self.assertEqual(table["source_rows"], [["기간", "\n".join(texts)]])
                    cell = next(c for c in table["cells"] if c["col"] == 1)
                    decision = cell["line_breaks"][0]
                    self.assertEqual(decision["separator"], " ")
                    self.assertEqual(decision["source_offset"], len(texts[0]))
                    self.assertEqual(decision["text_offset"], len(texts[0]))
                    self.assertIn(" ".join(texts), markdown)

    def test_date_range_wrapped_after_tilde_keeps_no_space(self):
        # '1.∼' 뒤에 자리가 남아도 다음 단어 '12.'가 통째로 넘어간 자동 줄바꿈이다.
        rows = self.cell_rows(
            (0, 0, 120, 40),
            [line("2019. 11. 1.∼", (5, 5, 70, 15)), line("12. 15.", (5, 20, 40, 30))],
        )
        self.assertEqual(rows, [["2019. 11. 1.∼12. 15."]])

    def test_ambiguous_word_wrap_preserves_boundary_and_warns(self):
        cell = (0, 0, 40, 40)
        warnings = []
        blocks, _ = P.extract_tables(TablePage([[cell]], cell),
            [line("관급", (5, 5, 23, 15)), line("자재", (5, 20, 23, 30))],
            P.Stats(), warnings)
        self.assertEqual(blocks[0].rows, [["관급 자재"]])
        self.assertEqual(blocks[0].source_rows, [["관급\n자재"]])
        self.assertEqual(blocks[0].cells[0]["line_breaks"][0]["reason"], "boundary_preserved")
        self.assertTrue(blocks[0].cells[0]["line_breaks"][0]["needs_review"])
        self.assertIn("table_cell_linebreak_review", {w["code"] for w in warnings})

    def test_range_join_is_explicit_and_does_not_swallow_new_items(self):
        for head, tail, expected in [
            ("2021. 10. 1.~", "10. 2.", "2021. 10. 1.~10. 2."),
            ("2021. 10. 1.~ ", "10. 2.", "2021. 10. 1.~ 10. 2."),
            ("2021. 10. 1.~", "1. 개요", "2021. 10. 1.~ 1. 개요"),
            ("문의-", "다음 항목", "문의- 다음 항목"),
        ]:
            with self.subTest(head=head, tail=tail):
                notes = []
                value, source = P.cell_text([
                    line(head, (5, 5, 90, 15), trailing=head.endswith(" ")),
                    line(tail, (5, 20, 90, 30))], P.Stats(), notes)
                self.assertEqual(value, expected)
                self.assertEqual(source, head + "\n" + tail)
                self.assertEqual(notes[0]["source_offset"], len(head))

    def test_centered_stacked_digits_join(self):
        digits = [
            line(d, (20, 5 + 15 * i, 26, 15 + 15 * i)) for i, d in enumerate("223")
        ]
        self.assertEqual(self.cell_rows((0, 0, 50, 60), digits), [["223"]])

    def test_single_character_symbols_do_not_jump_over_cell_text(self):
        tokens = ["∙", "∙", "전시", "∙", "(4건)", "∙"]
        lines = [line(text, (20, 5 + 15 * i, 30 + len(text) * 5, 15 + 15 * i))
                 for i, text in enumerate(tokens)]
        normalized, source = P.cell_text(lines, P.Stats())
        self.assertEqual(normalized.replace(" ", ""), "".join(tokens))
        self.assertEqual(source, "\n".join(tokens))

    def title_cells(self, marker, text, size):
        page = TablePage([[(0, 0, 30, 30), (30, 0, 300, 30)]], (0, 0, 300, 30))
        lines = [
            line(marker, (10, 10, 20, 20 + size), size=size),
            line(text, (40, 10, 100, 20 + size), size=size),
            line("본문 문장입니다", (0, 60, 200, 70)),
        ]
        blocks, _ = P.extract_tables(page, lines, P.Stats(), [])
        return blocks[0]

    def test_number_cell_table_at_body_size_keeps_cells(self):
        block = self.title_cells("1", "홍길동", 10)
        self.assertEqual(block.kind, "table")
        self.assertEqual(block.rows, [["1", "홍길동"]])

    def test_number_title_box_larger_than_body_is_heading(self):
        block = self.title_cells("3", "사업 내용", 16)
        self.assertEqual(
            (block.kind, block.marker, P.head_text(block)), ("table", "3", "사업 내용")
        )
        self.assertTrue(P.is_heading(block))
        self.assertEqual(block.rows, [["3", "사업 내용"]])
        self.assertEqual(len(block.cells), 2)
        self.assertEqual(block.source_rows, [["3", "사업 내용"]])
        body = P.Block("para", "본문", "본문", 1, (0, 80, 100, 90))
        P.assign_hierarchy([block, body], "doc")
        sections = P.build_sections([block, body], "doc")
        self.assertEqual(sections[0].head_text, "사업 내용")
        self.assertEqual(body.parent_id, block.id)
        self.assertIn("### 3 사업 내용", P.to_markdown("test", [block, body]))

    def test_roman_title_box_is_heading(self):
        block = self.title_cells("Ⅰ", "사업개요", 10)
        self.assertEqual(block.kind, "table")
        self.assertTrue(P.is_heading(block))

    def test_large_number_data_table_preserves_cells_without_heading(self):
        block = self.title_cells("1", "홍길동", 16)
        self.assertEqual(block.kind, "table")
        self.assertEqual(block.rows, [["1", "홍길동"]])
        self.assertEqual(len(block.cells), 2)
        self.assertFalse(P.is_heading(block))
        self.assertEqual(block.heading_hint["status"], "needs_review")
        self.assertIn("| 1 | 홍길동 |", P.to_markdown("test", [block]))

    def test_sparse_grid_table_is_kept_with_review_warning(self):
        rows = [
            [(c * 20, r * 20, (c + 1) * 20, (r + 1) * 20) for c in range(10)]
            for r in range(2)
        ]
        page = TablePage(rows, (0, 0, 200, 40))
        lines = [line("A", (2, 2, 8, 12)), line("B", (22, 22, 28, 32))]
        warnings = []
        blocks, _ = P.extract_tables(page, lines, P.Stats(), warnings)
        self.assertEqual(blocks[0].kind, "table")
        self.assertEqual([w["code"] for w in warnings], ["table_grid_sparse"])

    def test_restored_space_is_not_written_to_source(self):
        raw = raw_line("AB")
        raw["spans"][0]["chars"][1]["bbox"] = (20, 0, 25, 10)
        raw["bbox"] = (0, 0, 25, 10)
        source = P.build_line(raw, P.Stats())
        blocks, _ = P.extract_tables(
            TablePage([[(0, 0, 100, 100)]]), [source], P.Stats(), []
        )
        self.assertEqual(blocks[0].rows, [["A B"]])
        self.assertEqual(blocks[0].source_rows, [["AB"]])
        self.assertIn("space_restored", blocks[0].transformations)

    def test_boundary_word_has_one_owner(self):
        page = TablePage([[(0, 0, 50, 50), (50, 0, 100, 50)]])
        blocks, _ = P.extract_tables(page, [line("X", (45, 10, 55, 20))], P.Stats(), [])
        self.assertEqual(blocks[0].rows, [["X", ""]])

    def test_line_center_outside_table_does_not_hide_inside_word(self):
        source = line("LEFT RIGHT", (-100, 10, 90, 20))
        source.words = [
            P.Word("LEFT", (-100, 10, -80, 20), 0, 4),
            P.Word("RIGHT", (10, 10, 30, 20), 5, 10),
        ]
        blocks, remaining = P.extract_tables(
            TablePage([[(0, 0, 100, 100)]]), [source], P.Stats(), []
        )
        self.assertEqual(blocks[0].rows, [["RIGHT"]])
        self.assertEqual(remaining[0].text, "LEFT")
        self.assertEqual(remaining[0].source_text, "LEFT")

    def test_source_offsets_survive_multiple_table_projections(self):
        source = line("A B C", (0, 0, 100, 10))
        first = P.select_words(source, [0, 2])
        self.assertEqual(first.source_text, "A\nC")
        second = P.select_words(first, [1])
        self.assertEqual(second.source_text, "C")

    def test_empty_rows_columns_and_merged_geometry_survive(self):
        rows = [
            [(0, 0, 60, 30), None, (60, 0, 90, 30)],
            [(0, 30, 30, 60), (30, 30, 60, 60), (60, 30, 90, 60)],
        ]
        page = TablePage(rows, (0, 0, 90, 60))
        blocks, _ = P.extract_tables(page, [line("A", (5, 5, 15, 15))], P.Stats(), [])
        self.assertEqual(blocks[0].rows, [["A", "", ""], ["", "", ""]])
        self.assertEqual(blocks[0].cells[0]["colspan"], 2)
        self.assertEqual(blocks[0].cells[0]["bbox"], [0, 0, 60, 30])

    def test_heading_recovery_retains_marker(self):
        page = TablePage([[(0, 0, 30, 50), (30, 0, 100, 50)]])
        blocks, _ = P.extract_tables(
            page,
            [
                line("1", (5, 5, 15, 21), size=16),
                line("사업개요", (40, 5, 80, 21), size=16),
                line("본문 문장입니다", (0, 120, 100, 130)),
            ],
            P.Stats(),
            [],
        )
        self.assertEqual(blocks[0].kind, "table")
        self.assertTrue(P.is_heading(blocks[0]))
        self.assertEqual(
            (blocks[0].marker_raw, blocks[0].marker_normalized), ("1", "1.")
        )

    def test_glyph_outline_grid_does_not_steal_heading_marker(self):
        source = line("Ⅲ 평가결과", (10, 10, 90, 20))
        source.words = [
            P.Word("Ⅲ", (10, 10, 20, 20), 0, 1),
            P.Word("평가결과", (40, 10, 90, 20), 2, 6),
        ]
        blocks, remaining = P.extract_tables(
            TablePage([[(10, 10, 20, 20)]], (10, 10, 20, 20)), [source], P.Stats(), []
        )
        self.assertFalse(blocks)
        self.assertEqual(remaining[0].source_text, "Ⅲ 평가결과")

    def test_sparse_real_table_keeps_empty_columns(self):
        page = TablePage(
            [[(i * 10, 0, (i + 1) * 10, 20) for i in range(10)]], (0, 0, 100, 20)
        )
        blocks, _ = P.extract_tables(page, [line("DATA", (1, 1, 9, 9))], P.Stats(), [])
        self.assertEqual(blocks[0].rows, [["DATA"] + [""] * 9])

    def test_decorative_grid_cannot_split_roman_heading(self):
        source = line("Ⅱ 사업의 정의 및 조성방향", (0, 10, 150, 20))
        page = TablePage(
            [[(20 + i * 10, 0, 30 + i * 10, 30) for i in range(13)]],
            (20, 0, 150, 30),
        )
        blocks, remaining = P.extract_tables(page, [source], P.Stats(), [])
        self.assertFalse(blocks)
        self.assertEqual(remaining, [source])

    def test_partial_approval_grid_preserves_source_line(self):
        source = line("관 문화시설팀장 체육문화과장 행", (0, 10, 150, 20))
        fields = [line("문서번호", (0, 30, 30, 40)), line("결재일자", (0, 50, 30, 60))]
        page = TablePage([[(70, 0, 150, 45)]], (70, 0, 150, 45))
        warnings = []
        blocks, remaining = P.extract_tables(
            page, [source] + fields, P.Stats(), warnings,
            [{"kind": "approval_grid", "bbox": [0, 0, 150, 60], "status": "candidate"}],
        )
        self.assertFalse(blocks)
        self.assertEqual(remaining, [source] + fields)
        self.assertEqual(warnings[0]["code"], "table_geometry_ambiguous")

    def test_many_cut_lines_fall_back_without_losing_original_order(self):
        sources = [
            line("LEFT RIGHT", (0, 10 + i * 20, 100, 20 + i * 20)) for i in range(4)
        ]
        page = TablePage([[(50, 0, 100, 100)]], (50, 0, 100, 100))
        warnings = []
        blocks, remaining = P.extract_tables(page, sources, P.Stats(), warnings)
        self.assertFalse(blocks)
        self.assertEqual(
            [entry.source_text for entry in remaining],
            [entry.source_text for entry in sources],
        )
        self.assertEqual(warnings[0]["code"], "table_geometry_ambiguous")

    def test_partial_heading_body_is_not_a_single_cell_table(self):
        source = line("□ HEADING DETAIL", (0, 10, 100, 20))
        page = TablePage([[(60, 0, 100, 100)]], (60, 0, 100, 100))
        blocks, remaining = P.extract_tables(page, [source], P.Stats(), [])
        self.assertFalse(blocks)
        self.assertEqual(remaining[0].source_text, source.source_text)

    def test_sparse_keyword_table_is_kept_without_confirmed_form(self):
        rows = [
            [(c * 40, r * 20, (c + 1) * 40, (r + 1) * 20) for c in range(10)]
            for r in range(5)
        ]
        page = TablePage(rows, (0, 0, 400, 100))
        source = line("등록번호", (1, 1, 35, 10))
        stats = P.Stats()
        warnings = []
        blocks, remaining = P.extract_tables(page, [source], stats, warnings)
        self.assertEqual(blocks[0].kind, "table")
        self.assertIn("등록번호", blocks[0].source_text)
        self.assertFalse(blocks[0].excluded_from_retrieval)
        self.assertEqual(stats.ghost_grids, 0)
        self.assertIn("table_grid_sparse", {w["code"] for w in warnings})

    def test_real_pdf_merged_cells(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=250, height=250)
            for a, b in [
                ((20, 20), (200, 20)),
                ((20, 60), (200, 60)),
                ((20, 100), (200, 100)),
                ((20, 20), (20, 100)),
                ((200, 20), (200, 100)),
                ((80, 60), (80, 100)),
                ((140, 20), (140, 100)),
            ]:
                page.draw_line(a, b)
            for at, text in [
                ((30, 45), "MERGED"),
                ((150, 45), "C"),
                ((30, 85), "D"),
                ((90, 85), "E"),
                ((150, 85), "F"),
            ]:
                page.insert_text(at, text)
            blocks, remaining = P.extract_tables(
                page, P.extract_lines(page, P.Stats()), P.Stats(), []
            )
            self.assertEqual(blocks[0].rows, [["MERGED", "", "C"], ["D", "E", "F"]])
            self.assertEqual(blocks[0].cells[0]["colspan"], 2)
            self.assertFalse(remaining)

    def test_failed_table_detection_keeps_text_and_warning(self):
        lines = [line("TEXT")]
        warnings = []
        page = NS(number=0)
        page.find_tables = lambda: (_ for _ in ()).throw(ValueError("bad grid"))
        self.assertEqual(
            P.extract_tables(page, lines, P.Stats(), warnings), ([], lines)
        )
        self.assertEqual(warnings[0]["code"], "table_detection_failed")


class SpacedParagraphTests(unittest.TestCase):
    def para(self, source, starts):
        # starts: 원문에서 각 글자가 시작하는 위치. 화면 텍스트는 글자마다 공백으로 띄운다.
        words = [
            P.Word(source[i], (i * 10, 0, i * 10 + 8, 10), i, i + 1) for i in starts
        ]
        text = " ".join(word.text for word in words)
        return P.classify(P.Line(source, text, (0, 0, 100, 10), 10, words), 0, 1)

    def test_author_single_space_is_kept(self):
        block = self.para("그 외", [0, 2])
        self.assertEqual(block.text, "그 외")
        self.assertNotIn("spaced_title_collapsed", block.transformations)

    def test_restored_spaces_are_kept_in_paragraphs(self):
        block = self.para("공개구분", [0, 1, 2, 3])
        self.assertEqual(block.text, "공 개 구 분")
        self.assertNotIn("spaced_title_collapsed", block.transformations)

    def test_author_wide_spaces_remain_word_boundaries(self):
        for source, starts, expected in [
            ("그  외", [0, 3], "그 외"),
            ("7  개", [0, 3], "7 개"),
            ("파   주   시", [0, 4, 8], "파 주 시"),
        ]:
            with self.subTest(source=source):
                block = self.para(source, starts)
                self.assertEqual(block.text, expected)
                self.assertEqual(block.source_text, source)
                self.assertNotIn("spaced_title_collapsed", block.transformations)

    def test_confirmed_heading_can_collapse_letter_spacing(self):
        block = P.classify(line("1. 개 요"), 0, 1)
        self.assertEqual((block.kind, block.text), ("heading", "개요"))


class ApprovalRegionTests(unittest.TestCase):
    def document(self, *, signature=True, title_above=False, page_number=0, border=True):
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        for y in (80, 120, 160) if border else (80, 120):
            page.draw_line((50, y), (200, y))
        for x in (50, 110, 200):
            page.draw_line((x, 80), (x, 160))
        for text, x, y in [("문서번호", 60, 105), ("1234", 120, 105),
                           ("보존기간", 60, 145), ("5년", 120, 145)]:
            page.insert_text((x, y), text, fontname="korea", fontsize=10)
        if signature:
            for y in (80, 120, 160):
                page.draw_line((220, y), (380, y))
            for x in (220, 300, 380):
                page.draw_line((x, 80), (x, 160))
            for text, x, y in [("주무관", 230, 105), ("과장", 310, 105),
                               ("홍길동", 230, 145), ("김철수", 310, 145)]:
                page.insert_text((x, y), text, fontname="korea", fontsize=10)
        if title_above:
            page.insert_text((50, 65), "1. 추진 개요", fontname="korea", fontsize=16)
        for text, x, y in [("옆의 일반 본문입니다", 410, 105),
                           ("사업 추진을 위한 일반 본문입니다", 50, 180),
                           ("구체적인 추진 내용을 검토합니다", 50, 200)]:
            page.insert_text((x, y), text, fontname="korea", fontsize=10)
        # 다른 페이지에서는 동일한 서식도 자동 제외하지 않는다.
        if page_number:
            doc.new_page(pno=0)
        return doc

    def parse(self, **options):
        with self.document(**options) as doc, tempfile.TemporaryDirectory() as root:
            path = Path(root) / "approval.pdf"
            doc.save(path)
            return P.parse_pdf(path)

    def test_confirmed_form_excludes_only_its_rectangles(self):
        doc, markdown, _ = self.parse()
        approvals = [b for b in doc["blocks"] if b["kind"] == "approval"]
        self.assertEqual(len(approvals), 2)
        self.assertTrue(all(b["excluded_from_retrieval"] for b in approvals))
        for text in ("옆의 일반 본문입니다", "사업 추진을 위한 일반 본문입니다",
                     "구체적인 추진 내용을 검토합니다"):
            block = next(b for b in doc["blocks"] if text in b["text"])
            self.assertNotEqual(block["kind"], "approval")
            self.assertIn(text, markdown)
        self.assertFalse(doc["warnings"])

    def test_metadata_table_without_signatures_stays_in_markdown(self):
        doc, markdown, _ = self.parse(signature=False)
        self.assertFalse(any(b["kind"] == "approval" for b in doc["blocks"]))
        self.assertIn("문서번호", markdown)
        self.assertIn("approval_like_table", {w["code"] for w in doc["warnings"]})

    def test_form_after_body_start_is_not_excluded(self):
        doc, markdown, _ = self.parse(title_above=True)
        self.assertFalse(any(b["kind"] == "approval" for b in doc["blocks"]))
        self.assertIn("문서번호", markdown)

    def test_unmarked_body_before_form_prevents_exclusion(self):
        with self.document() as pdf, tempfile.TemporaryDirectory() as root:
            pdf[0].insert_text((50, 65), "일반 본문을 설명하는 표입니다", fontname="korea", fontsize=10)
            path = Path(root) / "body-first.pdf"
            pdf.save(path)
            doc, markdown, _ = P.parse_pdf(path)
        self.assertFalse(any(b["kind"] == "approval" for b in doc["blocks"]))
        self.assertIn("문서번호", markdown)
        self.assertIn("일반 본문을 설명하는 표입니다", markdown)

    def test_form_on_second_page_is_not_excluded(self):
        doc, markdown, _ = self.parse(page_number=1)
        self.assertFalse(any(b["kind"] == "approval" for b in doc["blocks"]))
        self.assertIn("문서번호", markdown)

    def test_incomplete_form_keeps_text_with_warning(self):
        doc, markdown, _ = self.parse(border=False)
        self.assertFalse(any(b["kind"] == "approval" for b in doc["blocks"]))
        self.assertIn("문서번호", markdown)
        self.assertIn("approval_region_ambiguous", {w["code"] for w in doc["warnings"]})

    def test_unruled_fields_do_not_swallow_unmarked_body(self):
        with pymupdf.open() as pdf, tempfile.TemporaryDirectory() as root:
            page = pdf.new_page()
            for text, y in [("문서번호 1234", 40), ("보존기간 5년", 60),
                            ("사업 추진을 위한 일반 본문입니다", 80),
                            ("구체적인 추진 내용을 검토합니다", 100)]:
                page.insert_text((50, y), text, fontname="korea", fontsize=10)
            path = Path(root) / "unruled.pdf"
            pdf.save(path)
            doc, markdown, _ = P.parse_pdf(path)
        self.assertFalse(any(b["kind"] == "approval" for b in doc["blocks"]))
        self.assertIn("구체적인 추진 내용을 검토합니다", markdown)
        self.assertIn("approval_region_ambiguous", {w["code"] for w in doc["warnings"]})

    def test_keyword_explanation_is_not_a_field_label(self):
        with self.document(signature=False) as pdf:
            page = pdf[0]
            lines = P.extract_lines(page, P.Stats())
            page.add_redact_annot((50, 80, 110, 160))
            page.apply_redactions()
            page.insert_text((55, 105), "문서번호 및 보존기간", fontname="korea", fontsize=6)
            regions = P.detect_approval_regions(page, P.extract_lines(page, P.Stats()), [])
            self.assertFalse(regions)

    def attached_body(self, *, hole=False):
        with self.document() as pdf, tempfile.TemporaryDirectory() as root:
            page = pdf[0]
            page.add_redact_annot((0, 161, 595, 842))
            page.apply_redactions()
            right = 110 if hole else 200
            for x in (50, right):
                page.draw_line((x, 160), (x, 200))
            page.draw_line((50, 200), (right, 200))
            quote = "경계밖본문검증" if hole else "본문보존검증문장"
            page.insert_text((115 if hole else 55, 185), quote, fontname="korea", fontsize=10)
            path = Path(root) / "attached.pdf"
            pdf.save(path)
            return P.parse_pdf(path), quote

    def test_body_cell_attached_to_metadata_grid_is_preserved(self):
        (doc, markdown, _), quote = self.attached_body()
        block = next(b for b in doc["blocks"] if quote in b["text"])
        self.assertEqual(block["kind"], "table")
        self.assertFalse(block.get("excluded_from_retrieval"))
        self.assertIn(quote, markdown)
        self.assertIn("approval_geometry_ambiguous", {w["code"] for w in doc["warnings"]})

    def test_glyphs_in_grid_hole_are_preserved_after_failed_recovery(self):
        (doc, markdown, _), quote = self.attached_body(hole=True)
        block = next(b for b in doc["blocks"] if quote in b["text"])
        self.assertNotEqual(block["kind"], "approval")
        self.assertFalse(block.get("excluded_from_retrieval"))
        self.assertIn(quote, markdown)
        self.assertIn("approval_geometry_ambiguous", {w["code"] for w in doc["warnings"]})
        uncertain = [r for r in doc["layout_regions"] if r["kind"] == "approval_grid" and r["status"] == "needs_review"]
        for region in uncertain:
            self.assertFalse(any(b["kind"] == "approval" and P.center_in(b["bbox"], region["bbox"])
                                 for b in doc["blocks"]))


class ContractAndIntegrationTests(unittest.TestCase):
    def test_invented_marker_is_a_failure(self):
        report = checks.Report()
        checks.check_marker_roundtrip(
            {
                "schema_version": P.SCHEMA_VERSION,
                "blocks": [
                    {
                        "id": "b1",
                        "marker": "1.",
                        "text": "내용",
                        "source_text": "1 내용",
                    }
                ],
            },
            "mutation",
            report,
        )
        self.assertIn("P05 원문 부호 불일치", report.violations)

    def test_wrong_existing_section_is_rejected(self):
        blocks = [
            P.Block("heading", "1. A", "A", 1, (0, 0, 10, 10), level=2),
            P.Block("para", "body", "body", 1, (0, 20, 10, 30)),
            P.Block("heading", "2. B", "B", 1, (0, 40, 10, 50), level=2),
            P.Block("para", "body", "body", 1, (0, 60, 10, 70)),
        ]
        P.assign_hierarchy(blocks, "doc")
        sections = P.build_sections(blocks, "doc")
        payload = {
            "blocks": [P.block_to_dict(b) for b in blocks],
            "sections": [P.section_to_dict(s) for s in sections],
        }
        payload["blocks"][1]["section_id"] = sections[1].section_id
        report = checks.Report()
        checks.check_sections(payload, "mutation", report)
        self.assertIn("C06 section_id 소유권 불일치", report.violations)

    def test_real_pdf_parse_and_determinism(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "report.pdf"
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((50, 100), "1. Overview")
                page.insert_text((50, 130), "Some body text.")
                doc.save(path)
            a, _, _ = P.parse_pdf(path)
            b, _, _ = P.parse_pdf(path)
            self.assertEqual(checks.strip_volatile(a), checks.strip_volatile(b))
            report = checks.Report()
            checks.check_schema(a, "generated", report, P.SCHEMA_VERSION)
            checks.check_sections(a, "generated", report)
            checks.check_normalization(a, "generated", report)
            checks.check_marker_roundtrip(a, "generated", report)
            self.assertFalse(report.violations)

    def test_rotated_page_keeps_body_number_near_bottom(self):
        # 글자 좌표는 회전 전 기준이다. 회전 후 높이(595)로 재면 y=600 이 쪽 번호 띠에 든다.
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "rotated.pdf"
            with pymupdf.open() as doc:
                page = doc.new_page(width=595, height=842)
                page.insert_text((50, 100), "1. Overview")
                page.insert_text((50, 600), "12")
                page.set_rotation(90)
                doc.save(path)
            payload, _, _ = P.parse_pdf(path)
        self.assertEqual(payload["pages"][0]["height"], 842)
        number = next(b for b in payload["blocks"] if b["text"] == "12")
        self.assertNotEqual(number["kind"], "page_number")

    def test_document_closes_when_extraction_raises(self):
        class Document:
            closed = False

            def __iter__(self):
                yield NS(
                    get_text=lambda: "",
                    number=0,
                    rect=pymupdf.Rect(0, 0, 100, 100),
                    derotation_matrix=pymupdf.Identity,
                )

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "input.pdf"
            path.write_bytes(b"fake")
            doc = Document()
            with (
                patch.object(P.pymupdf, "open", return_value=doc),
                patch.object(P, "extract_lines", side_effect=ValueError("failure")),
            ):
                with self.assertRaises(ValueError):
                    P.parse_pdf(path)
            self.assertTrue(doc.closed)

    def test_batch_preserves_paths_and_continues_after_bad_pdf(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            for folder in ("a", "b"):
                path = root / "in" / folder / "same.pdf"
                path.parent.mkdir(parents=True)
                with pymupdf.open() as doc:
                    doc.new_page().insert_text((50, 100), folder)
                    doc.save(path)
            (root / "in" / "bad.pdf").write_text("not a PDF")
            with patch(
                "sys.argv", ["parser.py", str(root / "in"), "-o", str(root / "out")]
            ):
                self.assertEqual(P.main(), 1)
            docs = [
                json.loads((root / "out" / f / "same.json").read_text())
                for f in ("a", "b")
            ]
            self.assertNotEqual(
                docs[0]["document"]["sha256"], docs[1]["document"]["sha256"]
            )

    def test_atomic_write_failure_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "report.json"
            path.write_text("old")
            with patch.object(P.os, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    P.atomic_write(path, "new")
            self.assertEqual(path.read_text(), "old")
            self.assertEqual(list(Path(root).glob(".parser-*")), [])
