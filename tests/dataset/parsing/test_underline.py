import unittest

import pymupdf

from bareum_ai.dataset.parsing import parser as P


class UnderlineTests(unittest.TestCase):
    def make(self, text="alpha beta", needle="beta", border=False):
        doc = pymupdf.open()
        page = doc.new_page(width=200, height=100)
        page.insert_text((20, 30), text, fontsize=12)
        rect = page.search_for(needle)[0]
        y = 70 if border else rect.y1 - 1
        page.draw_line(
            (10 if border else rect.x0, y), (150 if border else rect.x1, y), width=0.6
        )
        block = P.Block(
            kind="table",
            page=1,
            bbox=(10, 10, 150, 70),
            text=text,
            source_text=text,
            rows=[[text]],
            cells=[
                {
                    "row": 0,
                    "col": 0,
                    "rowspan": 1,
                    "colspan": 1,
                    "bbox": [10, 10, 150, 70],
                }
            ],
        )
        P.mark_table_underlines(page, [block])
        doc.close()
        return block

    def test_exact_span_and_plain_text_preserved(self):
        b = self.make()
        self.assertEqual(b.rows, [["alpha beta"]])
        self.assertEqual(
            b.cells[0]["decorations"],
            [{"kind": "underline", "start": 6, "end": 10, "text": "beta"}],
        )

    def test_table_border_is_not_underline(self):
        self.assertNotIn("decorations", self.make(border=True).cells[0])

    def test_ambiguous_repeated_word_not_guessed(self):
        self.assertNotIn("decorations", self.make("beta beta").cells[0])

    def test_markdown_retains_underline(self):
        b = self.make()
        self.assertIn("<u>beta</u>", "\n".join(P.table_md(b.rows, b.cells)))

    def test_markdown_without_cells_is_plain_table(self):
        self.assertEqual(
            P.table_md([["A", "B"], ["1", "2"]]),
            ["| A | B |", "| --- | --- |", "| 1 | 2 |"],
        )
