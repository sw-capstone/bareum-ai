import unittest
from unittest import mock

from bareum_ai import sections
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
