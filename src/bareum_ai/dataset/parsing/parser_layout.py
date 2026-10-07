"""선·상자·글자 좌표로 확인할 수 있는 문서 영역을 복원한다."""

from collections import defaultdict

ARROW_DIRECTIONS = {
    **dict.fromkeys("▶→➜►➔▷➡", "right"),
    **dict.fromkeys("◀←◄", "left"),
}


def contains(box, point, tolerance=0):
    x, y = point
    return (
        box[0] - tolerance <= x <= box[2] + tolerance
        and box[1] - tolerance <= y <= box[3] + tolerance
    )


def center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def bounds(boxes):
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def glyphs(page):
    return [
        {"text": c["c"], "bbox": list(c["bbox"])}
        for b in page.get_text("rawdict")["blocks"]
        for line in b.get("lines", [])
        for span in line["spans"]
        for c in span["chars"]
    ]


def glyph_text(chars):
    rows = []
    for char in sorted(chars, key=lambda c: (center(c["bbox"])[1], c["bbox"][0])):
        cy = center(char["bbox"])[1]
        row = next(
            (row for row in rows if abs(center(row[0]["bbox"])[1] - cy) < 2), None
        )
        if row is None:
            rows.append([char])
        else:
            row.append(char)
    return "\n".join(
        "".join(c["text"] for c in sorted(row, key=lambda c: c["bbox"][0])).strip()
        for row in rows
    ).strip()


def ruled_cells(drawings):
    horizontal = []
    vertical = []
    for drawing in drawings:
        if drawing.get("type") not in ("s", "fs"):
            continue
        for item in drawing["items"]:
            if item[0] == "l":
                a, b = item[1:3]
                if abs(a.y - b.y) < 0.5 and abs(a.x - b.x) > 10:
                    horizontal.append((round(a.y), min(a.x, b.x), max(a.x, b.x)))
                if abs(a.x - b.x) < 0.5 and abs(a.y - b.y) > 10:
                    vertical.append((round(a.x), min(a.y, b.y), max(a.y, b.y)))
            elif item[0] == "re" and drawing.get("type") in ("s", "fs"):
                r = item[1]
                horizontal.extend(
                    [(round(r.y0), r.x0, r.x1), (round(r.y1), r.x0, r.x1)]
                )
                vertical.extend([(round(r.x0), r.y0, r.y1), (round(r.x1), r.y0, r.y1)])
    horizontal = sorted(set(horizontal))
    vertical = sorted(set(vertical))
    if len(horizontal) + len(vertical) > 1200:
        raise ValueError("선분 수가 제한을 초과해 영역 복원을 보류합니다")
    ys = sorted({y for y, _, _ in horizontal})
    cells = []
    if len(ys) > 120:
        raise ValueError("수평 경계 수가 제한을 초과해 영역 복원을 보류합니다")
    for top in ys:
        for bottom in ys:
            if bottom - top < 12:
                continue
            xs = sorted(
                {x for x, y0, y1 in vertical if y0 <= top + 1 and y1 >= bottom - 1}
            )
            for left, right in zip(xs, xs[1:]):
                if right - left < 12:
                    continue

                def covers(y):
                    return any(
                        abs(hy - y) <= 1 and x0 <= left + 1 and x1 >= right - 1
                        for hy, x0, x1 in horizontal
                    )

                if not covers(top) or not covers(bottom):
                    continue
                if any(
                    top + 1 < hy < bottom - 1 and x0 <= left + 1 and x1 >= right - 1
                    for hy, x0, x1 in horizontal
                ):
                    continue
                cells.append([left, top, right, bottom])
    return cells


def components(cells):
    groups = []
    for box in cells:
        hits = [
            g
            for g in groups
            if any(
                abs(b[2] - box[0]) <= 1
                and min(b[3], box[3]) - max(b[1], box[1]) > 1
                or abs(box[2] - b[0]) <= 1
                and min(b[3], box[3]) - max(b[1], box[1]) > 1
                or abs(b[3] - box[1]) <= 1
                and min(b[2], box[2]) - max(b[0], box[0]) > 1
                or abs(box[3] - b[1]) <= 1
                and min(b[2], box[2]) - max(b[0], box[0]) > 1
                for b in g
            )
        ]
        merged = [box]
        for g in hits:
            merged.extend(g)
            groups.remove(g)
        groups.append(merged)
    return groups


def page_rect(page):
    """텍스트·도형 좌표와 같은 회전 전 기준의 페이지 영역. page.rect는 회전 후 기준이다."""
    return page.rect * page.derotation_matrix


def inspect_page(page, approval_bottom=None, approval_padding=30):
    drawings = page.get_drawings()
    chars = glyphs(page)
    rect = page_rect(page)
    cells = ruled_cells(drawings)
    regions = []
    if approval_bottom is not None:
        for group in components(
            [
                c
                for c in cells
                if c[1] < approval_bottom and c[3] <= approval_bottom + approval_padding
            ]
        ):
            if len(group) < 4:
                continue
            area = bounds(group)
            picked = [c for c in chars if contains(area, center(c["bbox"]))]
            if not picked:
                continue
            if any(
                sum(contains(box, center(c["bbox"])) for box in group) != 1
                for c in picked
                if c["text"].strip()
            ):
                continue
            nodes = [
                {
                    "bbox": box,
                    "text": glyph_text(
                        [c for c in picked if contains(box, center(c["bbox"]))]
                    ),
                }
                for box in sorted(group, key=lambda b: (b[1], b[0]))
            ]
            regions.append(
                {
                    "kind": "approval_grid",
                    "bbox": area,
                    "nodes": nodes,
                    "evidence": "closed_ruled_cells",
                    "status": "geometry_recovered",
                }
            )
    boxes = []
    for box in [g[0] for g in components(cells) if len(g) == 1]:
        if any(contains(r["bbox"], center(box)) for r in regions):
            continue
        if (
            35 <= box[2] - box[0] <= rect.width * 0.45
            and 20 <= box[3] - box[1] <= rect.height * 0.16
        ):
            boxes.append(box)
    for d in drawings:
        r = list(d["rect"])
        fill = d.get("fill")
        if (
            fill
            and min(fill) < 0.8
            and len(d["items"]) >= 4
            and 35 <= r[2] - r[0] <= rect.width * 0.4
            and 20 <= r[3] - r[1] <= rect.height * 0.16
        ) and not any(max(abs(a - b) for a, b in zip(r, x)) < 2 for x in boxes):
            boxes.append(r)
    boxes = [
        b
        for b in boxes
        if len(glyph_text([c for c in chars if contains(b, center(c["bbox"]))]).strip())
        >= 2
    ]
    used = set()
    for axis in ("horizontal", "vertical"):
        coord = 1 if axis == "horizontal" else 0
        for i, box in enumerate(boxes):
            if i in used:
                continue
            cluster = [
                j
                for j, b in enumerate(boxes)
                if j not in used and abs(center(b)[coord] - center(box)[coord]) < 6
            ]
            if len(cluster) < 3:
                continue
            cluster.sort(key=lambda j: center(boxes[j])[1 - coord])
            chain = [boxes[j] for j in cluster]
            if any(b[1 - coord] < a[3 - coord] + 2 for a, b in zip(chain, chain[1:])):
                continue
            used.update(cluster)
            nodes = [
                {
                    "bbox": b,
                    "text": glyph_text(
                        [c for c in chars if contains(b, center(c["bbox"]))]
                    ),
                }
                for b in chain
            ]
            regions.append(
                {
                    "kind": "diagram",
                    "axis": axis,
                    "bbox": bounds(chain),
                    "nodes": nodes,
                    "evidence": "aligned_text_boxes",
                    "status": "needs_review",
                    "connectors": [c for c in chars if c["text"] in ARROW_DIRECTIONS],
                }
            )
    return regions


def attach_regions(blocks, pages, document_id):
    regions = []
    by_page = defaultdict(list)
    for b in blocks:
        by_page[b.page].append(b)
    for page_no, page_regions in enumerate(pages, 1):
        page_blocks = by_page[page_no]
        for raw in page_regions:
            region = dict(
                raw, id=f"{document_id}_layout{len(regions) + 1:04d}", page=page_no
            )
            region["nodes"] = [
                dict(
                    n, id=f"{region['id']}_n{i + 1}", block_ids=[], detail_block_ids=[]
                )
                for i, n in enumerate(raw["nodes"])
            ]
            nodes = region["nodes"]
            for n in nodes:
                n["block_ids"] = [
                    b.id
                    for b in page_blocks
                    if contains(n["bbox"], center(b.bbox)) and b.kind != "figure"
                ]
            region["edges"] = []
            if region["kind"] == "approval_grid":
                tables = [
                    b
                    for b in page_blocks
                    if b.kind == "approval"
                    and b.rows
                    and max(abs(a - c) for a, c in zip(b.bbox, region["bbox"])) <= 1
                ]
                for n in nodes:
                    n["block_ids"] = []
                    for table in tables:
                        for cell in table.cells:
                            if cell["bbox"] == n["bbox"]:
                                n["block_ids"] = [table.id]
                                n["cell_ref"] = {
                                    "block_id": table.id,
                                    "row": cell["row"],
                                    "col": cell["col"],
                                }
            if region["kind"] == "diagram":
                for n in nodes:
                    for b in page_blocks:
                        if b.id in n["block_ids"]:
                            b.layout_node_id = n["id"]
                if region["axis"] == "vertical":
                    for i, n in enumerate(nodes):
                        cy = center(n["bbox"])[1]
                        lo = (
                            (center(nodes[i - 1]["bbox"])[1] + cy) / 2
                            if i
                            else n["bbox"][1] - 25
                        )
                        hi = (
                            (cy + center(nodes[i + 1]["bbox"])[1]) / 2
                            if i + 1 < len(nodes)
                            else n["bbox"][3] + 10
                        )
                        details = [
                            b
                            for b in page_blocks
                            if b.kind in ("para", "item", "note")
                            and b.bbox[0] >= n["bbox"][2] + 2
                            and lo <= center(b.bbox)[1] < hi
                        ]
                        n["detail_block_ids"] = [b.id for b in details]
                        roots = [b for b in page_blocks if b.id in n["block_ids"]]
                        parent = roots[0] if roots else None
                        for b in details:
                            b.layout_node_id = n["id"]
                            if parent and b.parent_id == parent.parent_id:
                                b.parent_id = parent.id
                        if parent:
                            for b in roots[1:]:
                                b.parent_id = parent.id
                for a, b in zip(nodes, nodes[1:]):
                    edge = {
                        "source": a["id"],
                        "target": b["id"],
                        "type": "reading_order",
                        "evidence": "box_alignment",
                    }
                    if region["axis"] == "horizontal":
                        arrows = [
                            x
                            for x in region.get("connectors", [])
                            if x["text"].strip() in ARROW_DIRECTIONS
                            and a["bbox"][2] <= center(x["bbox"])[0] <= b["bbox"][0]
                            and abs(center(x["bbox"])[1] - center(a["bbox"])[1])
                            < max(
                                a["bbox"][3] - a["bbox"][1], b["bbox"][3] - b["bbox"][1]
                            )
                            / 2
                        ]
                        if len(arrows) == 1:
                            edge.update(
                                type="directed",
                                evidence="text_arrow",
                                evidence_bbox=arrows[0]["bbox"],
                                evidence_text=arrows[0]["text"],
                            )
                            if ARROW_DIRECTIONS[arrows[0]["text"].strip()] == "left":
                                edge["source"], edge["target"] = (
                                    edge["target"],
                                    edge["source"],
                                )
                    region["edges"].append(edge)
            regions.append(region)
    return regions
