import unittest

from bareum_ai import sections
from bareum_ai.rma import request as R
from bareum_ai.rma.validate import locate, validate_block_response, validate_document_response
from fixtures import ann, document_response, parsed_doc, response_for, small_chunks

DOC_LABELS = {"document_type": "결과", "document_topics": []}


def codes(issues):
    return {i.code for i in issues}


class LocateTests(unittest.TestCase):
    def test_unique_quote(self):
        self.assertEqual(locate("가나다", "나", None), (1, 2))

    def test_repeated_quote_needs_occurrence(self):
        self.assertIsInstance(locate("가나가", "가", None), str)
        self.assertEqual(locate("가나가", "가", 2), (2, 3))

    def test_missing_quote(self):
        self.assertIsInstance(locate("가나다", "라", None), str)


class BlockResponseTests(unittest.TestCase):
    def setUp(self):
        small_chunks(self)
        doc = parsed_doc()
        self.requests = R.block_requests(doc, sections.tag_document(doc), DOC_LABELS)

    def pair(self, block_id):
        request = next(r for r in self.requests if block_id in [t["id"] for t in r["targets"]])
        response = response_for(request)
        block = next(b for b in response["blocks"] if b["block_id"] == block_id)
        return request, response, block

    def assertCode(self, code, request, response):
        self.assertIn(code, codes(validate_block_response(request, response)))

    def test_valid_responses_pass(self):
        for request in self.requests:
            with self.subTest(targets=request["target_block_ids"]):
                self.assertEqual(validate_block_response(request, response_for(request)), [])

    def test_v01_request_id(self):
        request, response, _ = self.pair("b3")
        response["request_id"] = "other"
        self.assertCode("V01", request, response)

    def test_v02_missing_target(self):
        request, response, _ = self.pair("b3")
        response["blocks"] = [b for b in response["blocks"] if b["block_id"] != "b3"]
        self.assertCode("V02", request, response)

    def test_v03_duplicate_annotation_id(self):
        request, response, block = self.pair("b3")
        block["annotations"][1]["annotation_id"] = "a1"
        self.assertCode("V03", request, response)

    def test_v04_primary_must_exist(self):
        request, response, block = self.pair("b3")
        block["primary_annotation_id"] = "a9"
        self.assertCode("V04", request, response)

    def test_v05_unknown_move(self):
        request, response, block = self.pair("b3")
        block["annotations"][1]["move"] = "PLAN"
        self.assertCode("V05", request, response)

    def test_v06_step_on_move_without_steps(self):
        request, response, block = self.pair("b3")
        block["annotations"][1]["steps"] = [{"step": "LAW", "evidence_span": {"quote": "계획"}}]
        self.assertCode("V06", request, response)

    def test_v06_determined_needs_steps(self):
        request, response, block = self.pair("b4")
        block["annotations"][0]["steps"] = []
        self.assertCode("V06", request, response)

    def test_v07_undetermined_needs_reason(self):
        request, response, block = self.pair("b3")
        block["annotations"][1] = ann("a2", "UNDETERMINED", "시스템을 도입할 계획")
        self.assertCode("V07", request, response)

    def test_v08_spec_needs_items(self):
        request, response, block = self.pair("b3")
        block["annotations"][1]["move"] = "SPEC"
        self.assertCode("V08", request, response)

    def test_v08_spec_field_must_be_allowed(self):
        request, response, block = self.pair("b3")
        block["annotations"][1] = ann(
            "a2", "SPEC", "시스템",
            spec_items=[{"spec_field": "예산", "raw_label": "시스템", "value": "시스템",
                         "evidence_span": {"quote": "시스템"}, "label_span": None, "reason": None}],
        )
        self.assertCode("V08", request, response)

    def test_v09_quote_not_in_text(self):
        request, response, block = self.pair("b3")
        block["annotations"][0]["evidence_span"]["quote"] = "법률에 따라"
        self.assertCode("V09", request, response)

    def test_v09_repeated_quote_needs_occurrence(self):
        request, response, block = self.pair("b4")
        block["annotations"][0]["evidence_span"] = {"quote": "이"}
        self.assertCode("V09", request, response)

    def table_row(self, response, row):
        table = next(b for b in response["blocks"] if b["block_id"] == "b6")["table"]
        return table["rows"][row]

    def test_v10_merge_origin_and_header_cells_are_allowed(self):
        request, response, _ = self.pair("b6")
        self.table_row(response, 2)["annotations"][0]["evidence_span"] = {"quote": "6월", "row": 1, "col": 0}
        self.table_row(response, 1)["annotations"][0]["evidence_span"] = {"quote": "추진 실적", "row": 0, "col": 1}
        self.assertEqual(validate_block_response(request, response), [])

    def test_v10_covered_cell_is_rejected(self):
        request, response, _ = self.pair("b6")
        self.table_row(response, 2)["annotations"][0]["evidence_span"] = {"quote": "6월", "row": 2, "col": 0}
        self.assertCode("V10", request, response)

    def test_v10_other_body_row_is_rejected(self):
        request, response, _ = self.pair("b6")
        self.table_row(response, 2)["annotations"][0]["evidence_span"] = {"quote": "담당자 교육 3회", "row": 1, "col": 1}
        self.assertCode("V10", request, response)

    def test_v11_body_row_needs_annotation(self):
        request, response, _ = self.pair("b6")
        row = self.table_row(response, 1)
        row["annotations"], row["primary_annotation_id"] = [], None
        self.assertCode("V11", request, response)

    def test_v11_every_row_once(self):
        request, response, _ = self.pair("b6")
        table = next(b for b in response["blocks"] if b["block_id"] == "b6")["table"]
        table["rows"].pop()
        self.assertCode("V11", request, response)

    def test_v11_header_row_has_no_annotations(self):
        request, response, _ = self.pair("b6")
        self.table_row(response, 0)["annotations"] = [ann("r0.a1", "OTHER", "구분", row=0, col=0)]
        self.assertCode("V11", request, response)

    def test_v12_only_heading_may_be_empty(self):
        request, response, block = self.pair("b3")
        block["annotations"], block["primary_annotation_id"] = [], None
        self.assertCode("V12", request, response)

    def test_v12_title_like_item_may_be_empty(self):
        request, response, block = self.pair("b3")
        next(t for t in request["targets"] if t["id"] == "b3")["title_like"] = True
        block["annotations"], block["primary_annotation_id"] = [], None
        self.assertNotIn("V12", codes(validate_block_response(request, response)))

    def test_v13_context_must_be_document_block(self):
        request, response, block = self.pair("b3")
        block["annotations"][0]["context_block_ids"] = ["b99"]
        self.assertCode("V13", request, response)


class DocumentResponseTests(unittest.TestCase):
    def setUp(self):
        small_chunks(self)
        doc = parsed_doc()
        tags = sections.tag_document(doc)
        self.request = R.document_request(doc, tags)
        self.texts = R.block_texts(doc, tags)

    def issues(self, **changes):
        return validate_document_response(self.request, {**document_response(self.request), **changes}, self.texts)

    def test_valid_response_passes(self):
        self.assertEqual(self.issues(), [])

    def test_document_type_must_be_known(self):
        self.assertIn("D01", codes(self.issues(document_type="보고")))

    def test_null_type_needs_reason(self):
        self.assertIn("D01", codes(self.issues(document_type=None, document_type_evidence=None)))

    def test_topics_need_one_evidence_each(self):
        self.assertIn("D01", codes(self.issues(document_topics=["교육"])))

    def test_evidence_must_exist_in_block(self):
        self.assertIn("D01", codes(self.issues(document_type_evidence={"block_id": "b1", "quote": "계획"})))

    def test_outline_must_be_known(self):
        self.assertIn("D01", codes(self.issues(outline_id="회의")))


if __name__ == "__main__":
    unittest.main()
