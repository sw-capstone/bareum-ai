import unittest

from bareum_ai import sections


class LookupTests(unittest.TestCase):
    def test_exact_match_after_normalization(self):
        self.assertEqual(sections.lookup("1. 추 진 배 경"), "SEC_BACKGROUND")

    def test_prefix_match(self):
        self.assertEqual(sections.lookup("추진결과 및 성과"), "SEC_RESULT")

    def test_value_after_colon_is_ignored(self):
        self.assertEqual(sections.lookup("사업개요 : 2026년 민원 개선"), "SEC_OVERVIEW")

    def test_unknown_heading(self):
        self.assertEqual(sections.lookup("우리 동네 이야기"), sections.UNKNOWN)

    def test_no_substring_match(self):
        # 포함 매칭을 쓰면 문서 제목이 추진계획 구간으로 잡힌다
        self.assertEqual(sections.lookup("2026년 시설운영계획"), sections.UNKNOWN)


class TagDocumentTests(unittest.TestCase):
    def test_attachment_head_is_annex(self):
        payload = {
            "blocks": [{"id": "b1", "kind": "attachment"}, {"id": "b2", "kind": "heading"}],
            "sections": [
                {"section_id": "s1", "head_block_id": "b1", "head_text": "추진배경"},
                {"section_id": "s2", "head_block_id": "b2", "head_text": "추진배경"},
            ],
        }
        self.assertEqual(
            sections.tag_document(payload), {"s1": sections.ANNEX, "s2": "SEC_BACKGROUND"}
        )


if __name__ == "__main__":
    unittest.main()
