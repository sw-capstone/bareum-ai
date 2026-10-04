import unittest
from pathlib import Path
from types import SimpleNamespace

import pymupdf
from parser_checks import requires_corpus, source_records, stable_payload

from bareum_ai.dataset.parsing import parser as P


class DecorativeHeadingTests(unittest.TestCase):
    def test_short_title_continuation_cannot_cross_page(self):
        title = P.Block(
            kind="para",
            source_text="보고서 제목",
            text="보고서 제목",
            page=1,
            bbox=(50, 20, 400, 50),
            size=24,
        )
        next_page = P.Block(
            kind="para",
            source_text="보고",
            text="보고",
            page=2,
            bbox=(200, 60, 250, 84),
            size=24,
        )
        body = [
            P.Block(
                kind="para",
                source_text="본문",
                text="본문",
                page=2,
                bbox=(50, 100 + i * 20, 300, 115 + i * 20),
                size=12,
            )
            for i in range(3)
        ]
        P.mark_document_title([title, next_page, *body], 842)
        self.assertEqual(title.text, "보고서 제목")
        self.assertEqual(next_page.kind, "para")

    def fixture(self, underline=True, light=True):
        drawings = [
            {
                "type": "f",
                "rect": pymupdf.Rect(50, 100, 78, 145),
                "fill": (0, 0.5, 0.75),
            }
        ]
        if underline:
            drawings.append(
                {
                    "type": "s",
                    "rect": pymupdf.Rect(84, 145, 160, 145),
                    "color": (0, 0.5, 0.75),
                }
            )
        page = SimpleNamespace(number=0, get_drawings=lambda: drawings)
        lines = [
            P.Line("운영개", "운 영 개", (90, 101, 156, 118), 17, []),
            P.Line("1", "1", (58, 112, 68, 129), 17, [], is_light=light),
            P.Line("요", "요", (116, 127, 133, 144), 17, []),
        ]
        return page, lines

    def test_colored_number_and_underlined_caption_form_heading(self):
        headings, remaining = P.recover_decorative_headings(*self.fixture())
        self.assertEqual(remaining, [])
        self.assertEqual(
            [(h.kind, h.marker, h.text) for h in headings],
            [("heading", "1", "운영개요")],
        )
        self.assertEqual(len(headings[0].source_fragments), 3)

    def test_plain_badge_or_dark_number_is_not_promoted(self):
        for options in ({"underline": False}, {"light": False}):
            page, lines = self.fixture(**options)
            self.assertEqual(P.recover_decorative_headings(page, lines), ([], lines))

    def test_intervening_text_prevents_merge(self):
        page, lines = self.fixture()
        lines.append(P.Line("주석", "주석", (95, 120, 110, 126), 9, []))
        self.assertFalse(P.recover_decorative_headings(page, lines)[0])

    @requires_corpus
    def test_real_source_titles_and_approval_table(self):
        records = source_records()
        record = next(r for r in records if r["document_id"] == "doc_6e7d9400b7f66b5a")
        doc, _, _ = P.parse_pdf(Path(record["pdf_path"]))
        again, _, _ = P.parse_pdf(Path(record["pdf_path"]))
        self.assertEqual(stable_payload(doc), stable_payload(again))
        self.assertEqual(
            [b["text"] for b in doc["blocks"] if b["kind"] == "document_title"],
            ["2020년 말라리아 감시 거점센터 운영 결과 보고"],
        )
        self.assertTrue(
            {"운영개요", "추진실적", "문제점및개선사항", "향후계획"}
            <= {s["head_text"] for s in doc["sections"]}
        )
        signatures = [b for b in doc["blocks"] if "주무관" in b.get("text", "")]
        self.assertTrue(signatures)
        self.assertTrue(
            all(
                b["kind"] == "approval" and b["excluded_from_retrieval"]
                for b in signatures
            )
        )
