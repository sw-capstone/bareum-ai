import unittest
from pathlib import Path

import pymupdf
from parser_checks import requires_corpus, source_records

from bareum_ai.dataset.parsing import parser as P


class DashedTableTests(unittest.TestCase):
    def drawings(self, starts, axis=0, opacity=1):
        return [
            {
                "type": "s",
                "color": (0, 0, 0),
                "width": 0.3,
                "stroke_opacity": opacity,
                "items": [
                    (
                        "l",
                        pymupdf.Point(x, 20) if axis == 0 else pymupdf.Point(20, x),
                        pymupdf.Point(x + 0.5, 20)
                        if axis == 0
                        else pymupdf.Point(20, x + 0.5),
                    )
                ],
            }
            for x in starts
        ]

    def test_dashes_recover_only_supported_span(self):
        for axis in (0, 1):
            lines = P.dashed_table_lines(
                self.drawings(range(10, 30), axis), [(0, 0, 40, 40)]
            )
            self.assertEqual(
                lines,
                [((10, 20), (29.5, 20))] if axis == 0 else [((20, 10), (20, 29.5))],
            )

    def test_real_gaps_outside_and_invisible_lines_are_not_bridged(self):
        self.assertEqual(
            P.dashed_table_lines(
                self.drawings([*range(10, 15), *range(25, 30)]), [(0, 0, 40, 40)]
            ),
            [],
        )
        self.assertEqual(
            P.dashed_table_lines(self.drawings(range(10, 30)), [(50, 50, 90, 90)]), []
        )
        self.assertEqual(
            P.dashed_table_lines(
                self.drawings(range(10, 30), opacity=0), [(0, 0, 40, 40)]
            ),
            [],
        )

    def test_solid_and_sparse_segments_do_not_invent_grid(self):
        for positions in (range(10, 14), range(0, 40, 4)):
            self.assertEqual(
                P.dashed_table_lines(self.drawings(positions), [(0, 0, 50, 50)]), []
            )

    @requires_corpus
    def test_reviewed_survey_has_five_columns_and_five_rows(self):
        records = source_records()
        record = next(r for r in records if r["document_id"] == "doc_645bfd07e6ac41ec")
        doc = P.parse_pdf(Path(record["pdf_path"]))[0]
        table = next(
            b for b in doc["blocks"] if b["page"] == 1 and b["kind"] == "table"
        )
        self.assertEqual(
            table["rows"],
            [
                ["구 분", "추진대상", "현장조사(건수)", "현황측량(건수)", "사 업 비"],
                ["계", "동작구 전역", "822", "784", "326,654,000"],
                [
                    "2018",
                    "대방동, 신대방동, 상도1동, 동작동",
                    "131",
                    "172",
                    "72,675,000",
                ],
                ["2017", "노량진동, 본동, 흑석동", "292", "240", "135,608,000"],
                ["2016", "상도동, 사당동", "399", "372", "118,371,000"],
            ],
        )
        self.assertEqual(len(table["cells"]), 25)
        self.assertTrue(all(c["rowspan"] == c["colspan"] == 1 for c in table["cells"]))
        self.assertIn("dashed_table_boundaries_recovered", table["transformations"])
