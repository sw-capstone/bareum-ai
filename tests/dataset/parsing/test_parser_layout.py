import tempfile
import unittest
from pathlib import Path

import parser_checks as A
import pymupdf

from bareum_ai.dataset.parsing import parser as P
from bareum_ai.dataset.parsing import parser_layout as L


class LayoutTests(unittest.TestCase):
    def test_grid_is_not_a_flow_diagram(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            for x in (40, 100, 160, 220):
                page.draw_line((x, 40), (x, 80))
            for y in (40, 80):
                page.draw_line((40, y), (220, y))
            for x in (45, 105, 165):
                page.insert_text((x, 65), "Text")
            self.assertFalse(L.inspect_page(page))

    def test_separate_boxes_are_nodes_without_invented_arrows(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            for x in (40, 160, 280):
                page.draw_rect((x, 40, x + 90, 90))
                page.insert_text((x + 10, 65), "Stage")
            raw = L.inspect_page(page)
            self.assertEqual(len(raw), 1)
            self.assertEqual(len(raw[0]["nodes"]), 3)
            regions = L.attach_regions([], [raw], "doc")
            self.assertTrue(
                all(e["type"] == "reading_order" for e in regions[0]["edges"])
            )

    def test_approval_grid_joins_spaced_glyphs_by_cell(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            for x in (40, 100, 160):
                page.draw_line((x, 40), (x, 110))
            for y in (40, 75, 110):
                page.draw_line((40, y), (160, y))
            for x, text in [(45, "A"), (65, "B"), (105, "C"), (125, "D")]:
                page.insert_text((x, 60), text)
            nodes = [
                {"bbox": box, "text": L.glyph_text([c for c in L.glyphs(page)
                    if L.contains(box, L.center(c["bbox"]))])}
                for box in sorted(L.ruled_cells(page.get_drawings()), key=lambda b: (b[1], b[0]))
            ]
            regions = L.inspect_page(page, [{"kind": "approval_grid", "bbox": [40, 40, 160, 110],
                                             "nodes": nodes, "status": "geometry_recovered"}])
            self.assertEqual(
                [n["text"] for n in regions[0]["nodes"]], ["AB", "CD", "", ""]
            )

    def test_incomplete_rectangle_is_not_recovered(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.draw_line((40, 40), (100, 40))
            page.draw_line((40, 40), (40, 90))
            self.assertFalse(L.ruled_cells(page.get_drawings()))

    def test_direction_uses_explicit_arrow_and_respects_left_arrow(self):
        nodes = [
            {"text": str(i), "bbox": [i * 100, 0, i * 100 + 80, 50]} for i in range(3)
        ]
        raw = {
            "kind": "diagram",
            "axis": "horizontal",
            "bbox": [0, 0, 280, 50],
            "nodes": nodes,
            "connectors": [{"text": "←", "bbox": [82, 20, 98, 30]}],
            "status": "needs_review",
        }
        region = L.attach_regions([], [[raw]], "doc")[0]
        self.assertEqual(region["edges"][0]["source"], region["nodes"][1]["id"])
        self.assertEqual(region["edges"][0]["type"], "directed")
        self.assertEqual(region["edges"][1]["type"], "reading_order")

    def test_conflicting_arrows_do_not_infer_direction(self):
        nodes = [
            {"text": str(i), "bbox": [i * 100, 0, i * 100 + 80, 50]} for i in range(3)
        ]
        raw = {
            "kind": "diagram",
            "axis": "horizontal",
            "bbox": [0, 0, 280, 50],
            "nodes": nodes,
            "connectors": [{"text": t, "bbox": [82, 20, 98, 30]} for t in ["→", "←"]],
        }
        self.assertEqual(
            L.attach_regions([], [[raw]], "doc")[0]["edges"][0]["type"], "reading_order"
        )

    def test_vertical_details_reparent_to_the_stage_not_heading(self):
        nodes = [
            {"text": str(i), "bbox": [0, i * 100, 80, i * 100 + 50]} for i in range(3)
        ]
        raw = {
            "kind": "diagram",
            "axis": "vertical",
            "bbox": [0, 0, 80, 250],
            "nodes": nodes,
        }
        blocks = []
        for i in range(3):
            blocks.extend(
                [
                    P.Block(
                        "para",
                        "Stage",
                        "Stage",
                        1,
                        (10, i * 100 + 10, 60, i * 100 + 25),
                        id=f"s{i}",
                        parent_id="heading",
                    ),
                    P.Block(
                        "para",
                        "Detail",
                        "Detail",
                        1,
                        (100, i * 100 + 10, 200, i * 100 + 25),
                        id=f"d{i}",
                        parent_id="heading",
                    ),
                ]
            )
        L.attach_regions(blocks, [[raw]], "doc")
        self.assertEqual([b.parent_id for b in blocks[1::2]], ["s0", "s1", "s2"])

    def test_cross_boundary_approval_preserves_existing_blocks(self):
        b = P.Block("approval", "ABC", "ABC", 1, (0, 0, 200, 30))
        region = {"kind": "approval_grid", "bbox": [50, 0, 150, 40], "nodes": [],
                  "status": "geometry_recovered"}
        self.assertEqual(P.recover_approval_blocks([b], [[region]]), [b])
        self.assertEqual(region["status"], "needs_review")

    def vertical(self, x, top, labels):
        nodes = [
            {"text": t, "bbox": [x, top + i * 100, x + 80, top + i * 100 + 50]}
            for i, t in enumerate(labels)
        ]
        bottom = nodes[-1]["bbox"][3]
        return {
            "kind": "diagram",
            "axis": "vertical",
            "bbox": [x, top, x + 80, bottom],
            "nodes": nodes,
        }

    def para(self, bid, box):
        return P.Block("para", bid, bid, 1, box, id=bid, parent_id="heading")

    def test_side_by_side_diagrams_keep_their_own_details(self):
        left, right = (
            self.vertical(0, 0, ["L0", "L1"]),
            self.vertical(300, 0, ["R0", "R1"]),
        )
        blocks = []
        for i in range(2):
            y = i * 100 + 10
            blocks += [
                self.para(f"ls{i}", (10, y, 60, y + 15)),
                self.para(f"ld{i}", (100, y, 200, y + 15)),
                self.para(f"rs{i}", (310, y, 360, y + 15)),
                self.para(f"rd{i}", (400, y, 500, y + 15)),
            ]
        regions = L.attach_regions(blocks, [[left, right]], "doc")
        details = [[n["detail_block_ids"] for n in r["nodes"]] for r in regions]
        self.assertEqual(details, [[["ld0"], ["ld1"]], [["rd0"], ["rd1"]]])
        parents = {b.id: b.parent_id for b in blocks}
        self.assertEqual((parents["ld1"], parents["rd1"]), ("ls1", "rs1"))
        self.assertEqual(parents["rs1"], "heading")

    def test_far_column_is_not_a_detail(self):
        far = self.para("far", (80 + L.DETAIL_MAX_GAP + 5, 10, 300, 25))
        regions = L.attach_regions([far], [[self.vertical(0, 0, ["A"])]], "doc")
        self.assertEqual(regions[0]["nodes"][0]["detail_block_ids"], [])
        self.assertEqual((far.parent_id, far.layout_node_id), ("heading", None))

    def test_shared_detail_owner_does_not_depend_on_region_order(self):
        upper, lower = self.vertical(0, 0, ["U"]), self.vertical(0, 30, ["D"])
        owners = set()
        for order in ([upper, lower], [lower, upper]):
            shared = self.para("x", (100, 35, 200, 45))
            regions = L.attach_regions([shared], [[dict(r) for r in order]], "doc")
            node = next(n for r in regions for n in r["nodes"] if n["detail_block_ids"])
            owners.add(node["text"])
        self.assertEqual(owners, {"U"})

    def test_markdown_prints_each_detail_once(self):
        detail = self.para("d", (100, 10, 200, 25))
        region = L.attach_regions([detail], [[self.vertical(0, 0, ["A", "B"])]], "doc")[
            0
        ]
        region["nodes"][1]["detail_block_ids"] = ["d"]
        self.assertEqual(P.to_markdown("t", [detail], [region]).count("  - d"), 1)

    def test_twin_vertical_diagrams_in_pdf_are_not_duplicated(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "twin.pdf"
            with pymupdf.open() as doc:
                page = doc.new_page(width=595, height=842)
                for x, label in ((60, "L"), (320, "R")):
                    for i in range(3):
                        y = 120 + i * 70
                        page.draw_rect((x, y, x + 90, y + 45))
                        page.insert_text((x + 10, y + 27), f"{label}stage{i}")
                        page.insert_text((x + 100, y + 27), f"{label}detail{i}")
                doc.save(path)
            payload, markdown, _ = P.parse_pdf(path)
        texts = {b["id"]: b["text"] for b in payload["blocks"]}
        details = [
            [texts[bid] for bid in n["detail_block_ids"]]
            for r in payload["layout_regions"]
            for n in r["nodes"]
        ]
        self.assertEqual(
            details,
            [[f"{s}detail{i}"] for s in "LR" for i in range(3)],
        )
        bullets = [
            line for line in markdown.splitlines() if line.strip().startswith("-")
        ]
        self.assertEqual(len(bullets), len(set(bullets)))

    def test_layout_reference_validator_rejects_dangling_edge(self):
        doc = {
            "document": {"page_count": 1},
            "pages": [{"page": 1}],
            "blocks": [],
            "layout_regions": [
                {
                    "id": "r",
                    "page": 1,
                    "nodes": [],
                    "edges": [{"source": "a", "target": "b", "type": "directed"}],
                }
            ],
        }
        self.assertIn("invalid layout edge endpoints", A.validate_relations(doc))

    def test_filled_gradient_edges_are_not_table_rulings(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            for x in range(40, 140):
                page.draw_rect((x, 40, x + 1, 80), color=None, fill=(0.5, 0.6, 0.7))
            self.assertFalse(L.ruled_cells(page.get_drawings()))

    def test_approval_keeps_original_fragments_and_passes_normalization(self):
        import parser_checks as checks

        blocks = [
            P.Block("approval", "VALUE", "VALUE", 1, (60, 5, 95, 15)),
            P.Block("approval", "LABEL", "LABEL", 1, (5, 5, 45, 15)),
        ]
        region = {
            "kind": "approval_grid",
            "status": "geometry_recovered",
            "bbox": [0, 0, 100, 20],
            "nodes": [
                {"bbox": [0, 0, 50, 20], "text": "LABEL"},
                {"bbox": [50, 0, 100, 20], "text": "VALUE"},
            ],
        }
        recovered = P.recover_approval_blocks(blocks, [[region]])[0]
        self.assertEqual(
            [x["text"] for x in recovered.source_fragments], ["VALUE", "LABEL"]
        )
        self.assertEqual(recovered.rows, [["LABEL", "VALUE"]])
        report = checks.Report()
        checks.check_normalization(
            {"blocks": [P.block_to_dict(recovered)]}, "fixture", report
        )
        self.assertFalse(report.violations)


class ApprovalRecoverySafetyTests(unittest.TestCase):
    def test_containing_body_table_is_not_reclassified(self):
        block = P.Block("table", "metadata and body", "metadata and body", 1,
                        (0, 0, 200, 100), rows=[["metadata"], ["body"]])
        region = {"kind": "approval_grid", "bbox": [0, 0, 100, 20],
                  "nodes": [{"bbox": [0, 0, 100, 20], "text": "metadata"}],
                  "status": "geometry_recovered"}
        result = P.recover_approval_blocks([block], [[region]])
        self.assertEqual(result, [block])
        self.assertEqual(block.kind, "table")
        self.assertFalse(block.excluded_from_retrieval)
        self.assertEqual(region["status"], "needs_review")
