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

    def find_tables(self):
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
            [line("1", (5, 5, 15, 15)), line("사업개요", (40, 5, 80, 15))],
            P.Stats(),
            [],
        )
        self.assertEqual(blocks[0].kind, "heading")
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
            page, [source] + fields, P.Stats(), warnings
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

    def test_sparse_cover_form_is_rejected_without_losing_lines(self):
        rows = [
            [(c * 40, r * 20, (c + 1) * 40, (r + 1) * 20) for c in range(10)]
            for r in range(5)
        ]
        page = TablePage(rows, (0, 0, 400, 100))
        source = line("등록번호", (1, 1, 35, 10))
        stats = P.Stats()
        blocks, remaining = P.extract_tables(page, [source], stats, [])
        self.assertFalse(blocks)
        self.assertEqual(remaining, [source])
        self.assertEqual(stats.ghost_grids, 1)

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


class ApprovalRegionTests(unittest.TestCase):
    FIELDS = [
        line("문서번호 1234", (0, 30, 80, 40)),
        line("보존기간 5년", (0, 50, 80, 60)),
    ]

    def test_extension_keeps_approval_lines(self):
        rows = [
            line("홍길동", (0, 70, 40, 80)),
            line("2019. 06. 07.", (0, 85, 80, 98), size=13),
            line("협", (0, 100, 13, 113), size=13),
        ]
        region = P.detect_approval_region(self.FIELDS + rows)
        self.assertEqual(region, (30, 113))

    def test_extension_stops_at_larger_title(self):
        title = line("2020년 사업 결과보고", (0, 70, 200, 88), size=18)
        body = line("내용", (0, 90, 40, 100))
        region = P.detect_approval_region(self.FIELDS + [title, body])
        self.assertEqual(region, (30, 60))

    def test_extension_stops_at_body_marker(self):
        body = [line("1. 추진 개요", (0, 70, 80, 80)), line("내용", (0, 85, 40, 95))]
        region = P.detect_approval_region(self.FIELDS + body)
        self.assertEqual(region, (30, 60))

    def test_lines_above_bottom_approval_are_body(self):
        title = line("2020년 사업 결과보고", (0, 100, 200, 115), size=15)
        fields = [
            line("문서번호 1234", (0, 600, 80, 610)),
            line("보존기간 5년", (0, 620, 80, 630)),
        ]
        pages = P.DocumentPages(
            [{"page": 1}], [""], [[title] + fields], [[]], [[]], [841.0]
        )
        blocks = P._assemble_blocks(pages, P.Stats())
        kinds = {block.text: block.kind for block in blocks}
        self.assertNotEqual(kinds["2020년 사업 결과보고"], "approval")
        self.assertEqual(kinds["문서번호 1234"], "approval")

    def approval_table(self, top):
        page = TablePage(
            [
                [(0, top, 50, top + 20), (50, top, 100, top + 20)],
                [(0, top + 20, 50, top + 40), (50, top + 20, 100, top + 40)],
            ],
            (0, top, 100, top + 40),
        )
        lines = [
            line("문서번호", (5, top + 5, 45, top + 15)),
            line("1234", (55, top + 5, 95, top + 15)),
            line("보존기간", (5, top + 25, 45, top + 35)),
            line("5년", (55, top + 25, 95, top + 35)),
        ]
        warnings = []
        blocks, _ = P.extract_tables(page, lines, P.Stats(), warnings)
        return blocks[0], warnings

    def test_approval_table_on_cover_top_is_excluded(self):
        block, warnings = self.approval_table(10)
        self.assertEqual(block.kind, "approval")
        self.assertTrue(block.excluded_from_retrieval)
        self.assertFalse(warnings)

    def test_approval_like_body_table_stays_table_with_warning(self):
        block, warnings = self.approval_table(600)
        self.assertEqual(block.kind, "table")
        self.assertFalse(block.excluded_from_retrieval)
        self.assertEqual(warnings[0]["code"], "approval_like_table")


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
