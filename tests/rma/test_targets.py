import unittest

from bareum_ai import sections
from bareum_ai.rma import targets as T
from fixtures import parsed_doc


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.doc = parsed_doc()
        self.classes = T.classify(self.doc, sections.tag_document(self.doc))

    def cls(self, block_id):
        return self.classes[block_id]["class"], self.classes[block_id]["reason"]

    def test_text_kinds_are_targets(self):
        for block_id in ("b2", "b3", "b4", "b5", "b9", "b12"):
            with self.subTest(block=block_id):
                self.assertEqual(self.cls(block_id)[0], T.TARGET)

    def test_body_table_is_labeled_by_rows(self):
        self.assertEqual(self.cls("b6"), (T.TABLE, None))

    def test_annex_table_is_not_targeted(self):
        self.assertEqual(self.cls("b10"), (T.NOT_TARGETED, "annex_table"))

    def test_vision_table_is_unreadable(self):
        self.assertEqual(self.cls("b11"), (T.UNREADABLE, "requires_vision"))

    def test_empty_text_block_is_context(self):
        next(b for b in self.doc["blocks"] if b["id"] == "b9")["text"] = ""  # 표시만 있는 붙임 머리
        classes = T.classify(self.doc, sections.tag_document(self.doc))
        self.assertEqual(classes["b9"], {"class": T.CONTEXT, "reason": "empty_text"})

    def test_title_is_context_and_excluded_kinds_are_dropped(self):
        self.assertEqual(self.cls("b1")[0], T.CONTEXT)
        self.assertEqual(self.cls("b7")[0], T.EXCLUDED)
        self.assertEqual(self.cls("b8")[0], T.EXCLUDED)

    def test_region_bbox_does_not_override_parser_final_exclusion(self):
        self.doc["layout_regions"] = [{"kind": "approval_grid", "page": 1,
                                       "status": "geometry_recovered", "bbox": [0, 0, 20, 20]}]
        classes = T.classify(self.doc, sections.tag_document(self.doc))
        self.assertEqual(classes["b3"]["class"], T.TARGET)
        next(b for b in self.doc["blocks"] if b["id"] == "b3")["excluded_from_retrieval"] = True
        self.assertEqual(T.classify(self.doc, sections.tag_document(self.doc))["b3"]["class"], T.EXCLUDED)

    def test_partial_overlap_does_not_exclude_body_table(self):
        self.doc["layout_regions"] = [{"kind": "approval_grid", "page": 1,
                                       "status": "geometry_recovered", "bbox": [5, 5, 20, 20]}]
        classes = T.classify(self.doc, sections.tag_document(self.doc))
        self.assertEqual(classes["b3"]["class"], T.TARGET)
        self.assertEqual(classes["b6"]["class"], T.TABLE)

    def test_unconfirmed_region_does_not_exclude_body(self):
        # 파서가 확정하지 못한 결재 영역은 파서가 본문에 남긴 것이므로 그대로 판정한다
        self.doc["layout_regions"] = [{"kind": "approval_grid", "page": 1,
                                       "status": "needs_review", "bbox": [0, 0, 20, 20]}]
        classes = T.classify(self.doc, sections.tag_document(self.doc))
        self.assertEqual(classes["b3"]["class"], T.TARGET)
        self.assertEqual(classes["b6"]["class"], T.TABLE)


class NormalizeTests(unittest.TestCase):
    def test_control_characters_removed_but_newline_kept(self):
        self.assertEqual(T.normalize("가\x01나\r\n다\t라"), "가나\n다\t라")


class TableRowsTests(unittest.TestCase):
    def test_merged_cell_is_inherited_from_origin(self):
        block = next(b for b in parsed_doc()["blocks"] if b["id"] == "b6")
        rows = T.table_rows(block)
        self.assertEqual(rows[2][0], {"col": 0, "text": "6월", "inherited_from": [1, 0]})
        self.assertEqual(rows[1][0], {"col": 0, "text": "6월"})

    def test_uncovered_empty_cell_stays_empty(self):
        block = {"kind": "table", "rows": [["a", ""]], "cells": [{"row": 0, "col": 0}]}
        self.assertEqual(T.table_rows(block)[0][1], {"col": 1, "text": ""})


class ChunkTests(unittest.TestCase):
    def setUp(self):
        self.doc = parsed_doc()
        self.classes = T.classify(self.doc, sections.tag_document(self.doc))

    def test_small_sections_are_packed_whole(self):
        self.assertEqual(
            T.chunks(self.doc, self.classes),
            [["b2", "b3", "b4", "b5", "b6", "b12", "b9"]],
        )

    def test_section_that_does_not_fit_starts_new_chunk(self):
        # 구간별 판정 단위: s001=3, s002=5(표 3행 포함), s003=1
        self.assertEqual(
            T.chunks(self.doc, self.classes, limit=5),
            [["b2", "b3", "b4"], ["b5", "b6", "b12"], ["b9"]],
        )

    def test_limit_splits_consecutive_blocks_but_not_tables(self):
        # b6 표는 3행이라 상한 2를 넘어도 단독 묶음이 된다
        self.assertEqual(
            T.chunks(self.doc, self.classes, limit=2),
            [["b2", "b3"], ["b4"], ["b5"], ["b6"], ["b12"], ["b9"]],
        )


if __name__ == "__main__":
    unittest.main()


class TitleLikeTests(unittest.TestCase):
    def test_heading_is_title_like(self):
        self.assertTrue(T.title_like({"kind": "heading", "text": "추진배경"}, False))

    def test_short_parent_item_is_title_like(self):
        self.assertTrue(T.title_like({"kind": "item", "text": "내용"}, True))

    def test_item_without_children_is_not(self):
        self.assertFalse(T.title_like({"kind": "item", "text": "자기소개서 작성 및 면접 스킬 교육"}, False))

    def test_sentence_item_is_not(self):
        self.assertFalse(T.title_like({"kind": "item", "text": "교육을 3회 실시함"}, True))
