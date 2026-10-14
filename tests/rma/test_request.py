import copy
import unittest
from unittest import mock

from bareum_ai import sections, specs
from bareum_ai.rma import request as R
from fixtures import parsed_doc, small_chunks

DOC_LABELS = {"document_type": "결과", "document_topics": []}


class DocumentRequestTests(unittest.TestCase):
    def setUp(self):
        small_chunks(self)
        self.doc = parsed_doc()
        self.tags = sections.tag_document(self.doc)

    def test_request_id_is_stable_and_bound_to_content(self):
        first = R.document_request(self.doc, self.tags)
        self.assertEqual(first["request_id"], R.document_request(self.doc, self.tags)["request_id"])
        self.doc["blocks"][2]["text"] = "바뀐 문장"
        self.assertNotEqual(first["request_id"], R.document_request(self.doc, self.tags)["request_id"])

    def test_text_excludes_dropped_blocks_and_normalizes(self):
        text = R.document_request(self.doc, self.tags)["text"]
        self.assertIn("[b1] 2026년 민원 처리 개선 결과", text)
        self.assertNotIn("[b7]", text)
        self.assertIn("[b12] 관련 규정에 따라 추진함.", text)
        self.assertIn("구분 | 추진 실적\n6월 | 담당자 교육 3회\n6월 | 점검 2회", text)

    def test_outline_ids_come_from_sections_spec(self):
        self.assertIn("결과", R.document_request(self.doc, self.tags)["outline_ids"])

    def test_outline_candidates_preserve_structure_and_unconfirmed_conditions(self):
        request = R.document_request(self.doc, self.tags)
        candidates = {c["outline_id"]: c for c in request["outline_candidates"]}
        self.assertEqual(set(candidates), set(request["outline_ids"]))
        self.assertIn({"any_of": ["SEC_STATUS", "SEC_PROBLEM"]}, candidates["기획"]["required"])
        self.assertEqual(candidates["행사교육"]["order"][1:3], ["SEC_BACKGROUND", "SEC_OVERVIEW"])
        self.assertTrue(all(c["applicability_conditions"] is None for c in candidates.values()))
        self.assertEqual(request["section_tag_names"]["SEC_BACKGROUND"], "추진 배경")

    def test_new_applicability_condition_reaches_request_and_changes_identity(self):
        original = R.document_request(self.doc, self.tags)
        load = specs.load
        section_spec = copy.deepcopy(load("sections"))
        conditions = ["테스트용으로 확인한 목차 적용 조건"]
        section_spec["outline_selection"]["applicability_conditions"]["결과"] = conditions
        with mock.patch.object(specs, "load", side_effect=lambda name: section_spec if name == "sections" else load(name)):
            changed = R.document_request(self.doc, self.tags)
        candidate = next(c for c in changed["outline_candidates"] if c["outline_id"] == "결과")
        self.assertEqual(candidate["applicability_conditions"], conditions)
        self.assertNotEqual(original["request_id"], changed["request_id"])
        self.assertNotEqual(original["sections_sha256"], changed["sections_sha256"])

    def test_execution_time_does_not_change_request_identity(self):
        self.doc["parser"] = {"stats": {"elapsed_seconds": 1}}
        first = R.document_request(self.doc, self.tags)
        self.doc["parser"]["stats"]["elapsed_seconds"] = 999
        self.assertEqual(first, R.document_request(self.doc, self.tags))


class BlockRequestTests(unittest.TestCase):
    def setUp(self):
        small_chunks(self)
        self.doc = parsed_doc()
        self.tags = sections.tag_document(self.doc)

    def test_one_request_per_chunk_with_targets(self):
        requests = R.block_requests(self.doc, self.tags, DOC_LABELS)
        self.assertEqual([r["target_block_ids"] for r in requests], [["b2", "b3", "b4"], ["b5", "b12"], ["b9"]])
        self.assertEqual(requests[1]["target_table_ids"], ["b6"])
        self.assertEqual(requests[0]["document"]["document_type"], "결과")

    def test_short_document_gets_full_text(self):
        request = R.block_requests(self.doc, self.tags, DOC_LABELS)[0]
        self.assertIn("full_text", request["context"])

    def test_long_document_gets_local_context(self):
        with mock.patch.object(R, "FULL_TEXT_LIMIT", 10):
            request = R.block_requests(self.doc, self.tags, DOC_LABELS)[2]
        ids = [b["id"] for b in request["context"]["blocks"]]
        self.assertNotIn("b9", ids)  # 대상 자신은 문맥에 넣지 않는다
        self.assertIn("b10", ids)  # 같은 섹션
        self.assertIn("b6", ids)  # 앞쪽 인접 블록
        self.assertNotIn("b7", ids)  # 제외 블록


if __name__ == "__main__":
    unittest.main()
