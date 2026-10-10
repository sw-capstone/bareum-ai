import unittest
from pathlib import Path

from parser_checks import requires_corpus, source_records

from bareum_ai.dataset.parsing import parser as P
from bareum_ai.dataset.parsing import parser_layout as L


class MixedSizeWrapTests(unittest.TestCase):
    def lines(self, comma=True, small_tail=True):
        text = "❍ 지원 대상 : 기관," if comma else "❍ 지원 대상 : 기관"
        return [
            P.Line(
                text,
                text,
                (50, 100, 550, 114),
                14,
                [
                    P.Word("❍", (50, 100, 64, 114)),
                    P.Word("기관,", (520, 100, 550, 109 if small_tail else 114)),
                ],
            ),
            P.Line(
                "군부대 등",
                "군부대 등",
                (50, 122, 100, 131),
                9,
                [
                    P.Word("군부대", (50, 122, 77, 131)),
                    P.Word("등", (90, 122, 99, 131)),
                ],
            ),
        ]

    def test_small_tail_continues_list_with_source_preserved(self):
        result = P.merge_wrapped(self.lines(), P.Stats())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "❍ 지원 대상 : 기관, 군부대 등")
        self.assertEqual(result[0].source_text, "❍ 지원 대상 : 기관,\n군부대 등")
        self.assertEqual(result[0].line_count, 2)

    def test_unrelated_small_text_stays_separate(self):
        for options in ({"comma": False}, {"small_tail": False}):
            with self.subTest(options=options):
                self.assertEqual(
                    len(P.merge_wrapped(self.lines(**options), P.Stats())), 2
                )
        lines = self.lines()
        lines[0].text = "일반 본문 기관,"
        self.assertEqual(len(P.merge_wrapped(lines, P.Stats())), 2)

    def test_barrier_new_marker_and_column_prevent_merge(self):
        self.assertEqual(
            len(P.merge_wrapped(self.lines(), P.Stats(), [(40, 116, 560, 120)])), 2
        )
        for text, box in [
            ("※ 주석", (50, 122, 100, 131)),
            ("다음 열", (300, 122, 350, 131)),
        ]:
            lines = self.lines()
            lines[1].text, lines[1].bbox = text, box
            self.assertEqual(len(P.merge_wrapped(lines, P.Stats())), 2)


class ArrowTests(unittest.TestCase):
    def region(self, connectors):
        return {
            "kind": "diagram",
            "axis": "horizontal",
            "bbox": [0, 0, 140, 30],
            "nodes": [{"bbox": [x, 0, x + 40, 30], "text": str(x)} for x in (0, 100)],
            "connectors": connectors,
        }

    def edges(self, connectors):
        return L.attach_regions([], [[self.region(connectors)]], "doc_test")[0]["edges"]

    def test_supported_arrows_and_reverse_direction(self):
        for symbol, direction in L.ARROW_DIRECTIONS.items():
            with self.subTest(symbol=symbol):
                edge = self.edges([{"text": symbol, "bbox": [60, 8, 80, 22]}])[0]
                self.assertEqual(edge["type"], "directed")
                self.assertEqual(edge["evidence_text"], symbol)
                self.assertTrue(
                    edge["source"].endswith("_n1" if direction == "right" else "_n2")
                )

    def test_missing_ambiguous_or_off_axis_arrow_is_reading_order(self):
        arrow = {"text": "➡", "bbox": [60, 8, 80, 22]}
        for connectors in (
            [],
            [arrow, arrow],
            [{"text": "➡", "bbox": [60, 50, 80, 64]}],
        ):
            self.assertEqual(self.edges(connectors)[0]["type"], "reading_order")


@requires_corpus
class ReviewedSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = source_records()

    def parse(self, identity):
        record = next(r for r in self.records if r["document_id"] == identity)
        return P.parse_pdf(Path(record["pdf_path"]))[0]

    def test_malaria_item_keeps_smaller_continuation(self):
        doc = self.parse("doc_6e7d9400b7f66b5a")
        block = next(b for b in doc["blocks"] if "신속진단키트 무료배부" in b["text"])
        self.assertEqual(block["kind"], "item")
        self.assertTrue(block["text"].endswith("의료기관, 군부대 등"))
        self.assertIn("\n", block["source_text"])
        self.assertFalse(any(b["text"] == "군부대 등" for b in doc["blocks"]))

    def test_newspaper_four_stages_have_three_directed_edges(self):
        doc = self.parse("doc_5366ca993a75bb46")
        region = next(r for r in doc["layout_regions"] if r["kind"] == "diagram")
        self.assertEqual(
            [n["text"].replace("\n", " ") for n in region["nodes"]],
            [
                "보조사업자 공모 (e나라도움)",
                "신청 접수 및 심사",
                "보조사업자 선정",
                "보조금교부 및 사업수행",
            ],
        )
        self.assertEqual(len(region["edges"]), 3)
        for i, edge in enumerate(region["edges"]):
            self.assertEqual(edge["type"], "directed")
            self.assertEqual(edge["evidence_text"], "➡")
            self.assertEqual(
                (edge["source"], edge["target"]),
                (region["nodes"][i]["id"], region["nodes"][i + 1]["id"]),
            )
