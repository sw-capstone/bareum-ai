import unittest
from types import SimpleNamespace

import pymupdf

from bareum_ai.dataset.parsing import parser as P


class TableVisualTests(unittest.TestCase):
    def table(self):
        return P.Block(
            kind="table",
            page=1,
            bbox=(0, 0, 100, 100),
            text="표",
            source_text="표",
            line_count=1,
        )

    def drawing(self, color=(1, 0, 0), rect=(10, 10, 25, 25), opacity=1):
        return {
            "rect": pymupdf.Rect(rect),
            "fill": color,
            "fill_opacity": opacity,
            "items": [("re", pymupdf.Rect(rect), 1)],
        }

    def check(self, drawings=(), figures=()):
        table = self.table()
        page = SimpleNamespace(get_drawings=lambda: list(drawings), number=0)
        warnings = []
        P.mark_visual_tables(page, [table], list(figures), warnings)
        return table, warnings

    def test_embedded_image_marks_table(self):
        figure = P.Block(
            kind="figure",
            page=1,
            bbox=(15, 15, 40, 30),
            text="",
            source_text="",
            line_count=0,
        )
        table, warnings = self.check(figures=[figure])
        self.assertTrue(table.requires_vision)
        self.assertEqual(warnings[0]["code"], "table_visual_content")
        self.assertEqual(table.text, "표")

    def test_multiple_legend_colors_require_review(self):
        table, warnings = self.check(
            [
                self.drawing(c, rect=(10 + 20 * i, 10, 25 + 20 * i, 25))
                for i, c in enumerate([(1, 0, 0), (0, 1, 0), (0, 0, 1)])
            ]
        )
        self.assertTrue(table.requires_vision)
        self.assertEqual(len(warnings), 1)

    def test_single_header_fill_is_not_a_chart(self):
        table, warnings = self.check([self.drawing(rect=(0, 0, 100, 20))])
        self.assertFalse(table.requires_vision)
        self.assertEqual(warnings, [])

    def test_invisible_gray_and_outside_shapes_ignored(self):
        table, warnings = self.check(
            [
                self.drawing(opacity=0),
                self.drawing(color=(0.5, 0.5, 0.5)),
                self.drawing(rect=(150, 150, 170, 170)),
            ]
        )
        self.assertFalse(table.requires_vision)
        self.assertEqual(warnings, [])

    def test_table_without_visual_content_unchanged(self):
        table, warnings = self.check()
        self.assertFalse(table.requires_vision)
        self.assertEqual(warnings, [])
