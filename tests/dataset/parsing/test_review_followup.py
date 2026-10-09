"""리뷰 재검증에서 발견된 배경 격자·결재란·기관명 회귀."""

import tempfile
import unicodedata
import unittest
from pathlib import Path

import pymupdf
from parser_checks import requires_corpus, source_records

from bareum_ai.dataset.parsing import parser as P
from bareum_ai.dataset.parsing import parser_layout as L


def parse_document(pdf):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "review.pdf"
        pdf.save(path)
        return P.parse_pdf(path)


def draw_grid(page, xs, ys, *, filled=False):
    for x in xs:
        if filled:
            page.draw_rect((x, ys[0], x + 0.5, ys[-1]), color=None, fill=(0, 0, 0))
        else:
            page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        if filled:
            page.draw_rect((xs[0], y, xs[-1], y + 0.5), color=None, fill=(0, 0, 0))
        else:
            page.draw_line((xs[0], y), (xs[-1], y))


class ReviewFollowupTests(unittest.TestCase):
    def test_background_grid_releases_title_and_keeps_separate_tables(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            for x in range(40, 541, 20):
                page.draw_rect((x, 50, x + 10, 730), color=None, fill=(0.95, 0.95, 0.95))
            for y in range(50, 731, 20):
                page.draw_rect((40, y, 540, y + 10), color=None, fill=(0.95, 0.95, 0.95))
            for xs in ([60, 130, 220], [280, 350, 500]):
                draw_grid(page, xs, [90, 125, 160])
                page.insert_text((xs[0] + 5, 110), "항목", fontname="korea", fontsize=10)
                page.insert_text((xs[1] + 5, 145), "값", fontname="korea", fontsize=10)
            page.insert_text((60, 330), "무대 시설 운영 계획", fontname="korea", fontsize=22)
            for y in (390, 420, 450):
                page.insert_text((60, y), "반드시 보존할 본문입니다", fontname="korea", fontsize=10)
            doc, markdown, _ = parse_document(pdf)
        self.assertEqual([b['text'] for b in doc['blocks'] if b['kind'] == 'document_title'],
                         ['무대 시설 운영 계획'])
        tables = [b for b in doc['blocks'] if b['kind'] == 'table']
        self.assertEqual(len(tables), 2)
        self.assertTrue(all(len(b['rows']) == 2 and len(b['rows'][0]) == 2 for b in tables))
        self.assertIn('반드시 보존할 본문입니다', markdown)
        self.assertIn('table_background_grid_split', {w['code'] for w in doc['warnings']})

    def test_real_sparse_tables_keep_empty_cells_for_stroked_and_filled_borders(self):
        for filled in (False, True):
            with self.subTest(filled=filled), pymupdf.open() as pdf:
                page = pdf.new_page()
                draw_grid(page, list(range(50, 451, 40)), [80, 120, 160], filled=filled)
                page.insert_text((55, 105), 'DATA', fontsize=8)
                doc, _, _ = parse_document(pdf)
            table = next(b for b in doc['blocks'] if b['kind'] == 'table')
            self.assertEqual(len(table['rows']), 2)
            self.assertEqual(len(table['rows'][0]), 10)
            self.assertEqual(table['rows'][0][0], 'DATA')
            self.assertIn('table_grid_sparse', {w['code'] for w in doc['warnings']})

    def approval_document(self, gap=55, *, intervening=False):
        pdf = pymupdf.open()
        page = pdf.new_page(width=595, height=842)
        draw_grid(page, [50, 110, 200], [80, 120, 160])
        for text, x, y in [('문서번호', 55, 105), ('123', 115, 105),
                           ('보존기간', 55, 145), ('5년', 115, 145)]:
            page.insert_text((x, y), text, fontname='korea', fontsize=10)
        left = 200 + gap
        draw_grid(page, [left, left + 110, left + 220], [80, 120, 160])
        page.insert_text((left + 5, 105), '안전재난담당관', fontname='korea', fontsize=10)
        page.insert_text((left + 115, 105), '주무관', fontname='korea', fontsize=10)
        if intervening:
            page.insert_text((205, 110), '본문', fontname='korea', fontsize=10)
        page.insert_text((50, 185), '1. 사업 개요', fontname='korea', fontsize=16)
        page.insert_text((50, 220), '반드시 보존할 본문입니다', fontname='korea', fontsize=10)
        return pdf

    def test_aligned_approval_with_wide_gap_and_officer_is_recognized(self):
        with self.approval_document() as pdf:
            doc, markdown, _ = parse_document(pdf)
        self.assertEqual(sum(b['kind'] == 'approval' for b in doc['blocks']), 2)
        self.assertNotIn('문서번호', markdown)
        self.assertIn('반드시 보존할 본문입니다', markdown)

    def test_wide_gap_does_not_connect_tables_across_body_or_far_columns(self):
        for options in ({'intervening': True}, {'gap': 85}):
            with self.subTest(options=options), self.approval_document(**options) as pdf:
                doc, markdown, _ = parse_document(pdf)
            self.assertFalse(any(b['kind'] == 'approval' for b in doc['blocks']))
            self.assertIn('문서번호', markdown)

    def test_near_duplicate_rulings_do_not_duplicate_cell_ownership(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            draw_grid(page, [50, 100, 200], [80, 120.4, 160])
            page.draw_line((50, 120.6), (200, 120.6))
            cells = L.ruled_cells(page.get_drawings())
        self.assertEqual(len(cells), 4)
        self.assertEqual(sum(L.contains(c, (75, 100)) for c in cells), 1)
        self.assertEqual(sum(L.contains(c, (75, 140)) for c in cells), 1)

    def test_background_image_does_not_block_or_get_consumed_by_approval_recovery(self):
        text = P.Block('table', '주무관', '주무관', 1, (50, 80, 200, 160))
        image = P.Block('figure', '', '', 1, (0, 60, 595, 180))
        region = {'kind': 'approval_grid', 'status': 'candidate', 'recoverable': True,
                  'bbox': [50, 80, 200, 160],
                  'nodes': [{'bbox': [50, 80, 200, 160], 'text': '주무관'}]}
        blocks = P.recover_approval_blocks([text, image], [[region]])
        self.assertEqual(sum(b.kind == 'approval' for b in blocks), 1)
        self.assertIn(image, blocks)
        self.assertFalse(image.excluded_from_retrieval)

    def test_spaced_organization_stays_in_body_and_real_title_is_selected(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((50, 70), '파   주   시', fontname='korea', fontsize=25)
            page.insert_text((50, 160), '건립공사 정상화 계획', fontname='korea', fontsize=18)
            for y in (210, 240, 270):
                page.insert_text((50, y), '반드시 보존할 본문입니다', fontname='korea', fontsize=10)
            doc, markdown, _ = parse_document(pdf)
        self.assertEqual([b['text'] for b in doc['blocks'] if b['kind'] == 'document_title'],
                         ['건립공사 정상화 계획'])
        org = next(b for b in doc['blocks'] if b['text'] == '파 주 시')
        self.assertEqual(org['kind'], 'para')
        self.assertEqual(org['source_text'], '파   주   시')
        self.assertIn('파 주 시', markdown)

    @requires_corpus
    def test_review_source_documents(self):
        cases = [
            ('경기국악원 무대 시설 운영 계획(안)_A', '202X년 □□□□□ □□시설 운영 계획'),
            ('종합관광센터 건립공사 정상화 계획_A', '한반도 생태평화 종합관광센터 건립공사 정상화 계획'),
            ('실행력 강화 추진방안_안전재난담당관_B', '2019년 국가안전대진단 추진 계획'),
            ('추진계획_시민안전과_B', '2019년 국가안전대진단 추진계획'),
        ]
        records = source_records()
        for filename, title in cases:
            with self.subTest(filename=filename):
                record = next(r for r in records if filename in unicodedata.normalize('NFC', r['filename']))
                doc, markdown, _ = P.parse_pdf(Path(record['pdf_path']))
                self.assertEqual([b['text'] for b in doc['blocks'] if b['kind'] == 'document_title'], [title])
                approvals = [b for b in doc['blocks'] if b['page'] == 1 and b['kind'] == 'approval']
                self.assertGreaterEqual(len(approvals), 2)
                self.assertTrue(all(b.get('excluded_from_retrieval') for b in approvals))
                self.assertFalse(any(b.get('rows') and len(b['rows']) == 93 for b in doc['blocks']))
                self.assertIn(title, markdown)
                if '관광센터' in filename:
                    org = next(b for b in doc['blocks'] if b['text'] == '파 주 시')
                    self.assertEqual(org['kind'], 'para')
