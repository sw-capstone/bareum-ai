import unittest
import copy

from bareum_ai import sections
from bareum_ai.rma import request as R
from bareum_ai.rma.assemble import ANNOTATIONS, assemble
from fixtures import document_response, parsed_doc, response_for, small_chunks

RUN = {"run_id": "test-run", "labeler": {"type": "human", "id": "tester"}}


class AssembleTests(unittest.TestCase):
    def setUp(self):
        small_chunks(self)
        self.doc = parsed_doc()
        self.tags = sections.tag_document(self.doc)
        self.doc_request = R.document_request(self.doc, self.tags)
        doc_response = document_response(self.doc_request)
        requests = R.block_requests(self.doc, self.tags, doc_response)
        self.failed = [{"request_id": requests[2]["request_id"], "block_ids": ["b9"], "issue_codes": ["V09"]}]
        self.result = assemble(
            self.doc, self.tags, self.doc_request, doc_response,
            [(r, response_for(r)) for r in requests[:2]], self.failed, RUN,
        )
        self.blocks = {b["block_id"]: b for b in self.result["blocks"]}

    def test_spans_get_offsets(self):
        span = self.blocks["b3"]["annotations"][0]["evidence_span"]
        self.assertEqual(span, {"start": 0, "end": 9, "quote": "관련 조례에 따라"})
        step_span = self.blocks["b3"]["annotations"][0]["steps"][0]["evidence_span"]
        self.assertEqual((step_span["start"], step_span["end"]), (0, 5))

    def test_offsets_use_normalized_text(self):
        span = self.blocks["b12"]["annotations"][0]["evidence_span"]
        self.assertEqual((span["start"], span["end"]), (0, 9))

    def test_table_spans_keep_cell_position(self):
        row = self.blocks["b6"]["table"]["rows"][1]
        self.assertEqual(
            row["annotations"][0]["evidence_span"],
            {"row": 1, "col": 1, "start": 0, "end": 9, "quote": "담당자 교육 3회"},
        )
        self.assertEqual(self.blocks["b6"]["annotations"], [])

    def test_prefilled_tables(self):
        self.assertEqual(self.blocks["b10"]["table"]["table_status"], "not_targeted")
        self.assertEqual(self.blocks["b10"]["table"]["table_reason"], "annex_table")
        self.assertEqual(self.blocks["b11"]["table"]["table_status"], "unreadable")

    def test_failed_and_excluded_blocks_are_absent(self):
        for block_id in ("b1", "b7", "b8", "b9"):
            with self.subTest(block=block_id):
                self.assertNotIn(block_id, self.blocks)
        self.assertEqual(self.result["failed_units"], self.failed)

    def test_document_fields_and_records(self):
        self.assertEqual(self.result["schema_version"], ANNOTATIONS)
        self.assertEqual(self.result["document"]["document_type"], "결과")
        self.assertEqual(
            self.result["document"]["document_type_evidence"],
            {"block_id": "b1", "start": 12, "end": 17, "quote": "개선 결과"},
        )
        self.assertEqual(self.result["request_ids"][0], self.doc_request["request_id"])
        self.assertEqual(self.result["specs"]["parser_schema_version"], "1.0.0")

    def test_provenance_and_block_sources_are_recorded_once(self):
        self.assertEqual(self.result["snapshot"]["document_id"], "doc_t")
        self.assertEqual(self.result["block_sources"]["b3"]["source_text"], self.doc["blocks"][2]["source_text"])
        self.assertEqual(self.result["block_sources"]["b3"]["bbox"], [0, 0, 10, 10])
        self.assertIn("normalize(block.text)", self.result["span_basis"]["block"])

    def test_request_from_other_parse_is_rejected(self):
        other = copy.deepcopy(self.doc_request)
        other["parsed_sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "다른 파싱"):
            assemble(self.doc, self.tags, other, document_response(other), [], [], RUN)

    def test_request_from_other_specs_is_rejected(self):
        for key in ("moves_sha256", "sections_sha256"):
            other = copy.deepcopy(self.doc_request)
            other[key] = "different"
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "명세"):
                assemble(self.doc, self.tags, other, document_response(other), [], [], RUN)


if __name__ == "__main__":
    unittest.main()
