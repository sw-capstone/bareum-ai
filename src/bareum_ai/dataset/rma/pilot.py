"""RMA 파일럿 문서·섹션을 고르고 요청을 고정한다.

    uv run python -m bareum_ai.dataset.rma.pilot --parsed-root <파서 출력 루트> --work <작업 폴더>

Dev에서만 고른다. 고른 섹션의 판정 단위만 라벨링하며, 요청은 이 시점의 명세로 만들어
<작업 폴더>/requests/에 저장한다. 명세 자체는 복사하지 않으며, 검토 도구는 생성 당시와
현재 명세의 해시가 다르면 작업을 거절한다. 판정 기준 변경 시 새 작업 폴더를 만든다.
선정 기준은 docs/specs/rma-labeling-pipeline.md 11절이다.
"""

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import random
import re
import unicodedata

from bareum_ai import sections
from bareum_ai.dataset.parsing import snapshot
from bareum_ai.dataset.rma import split_files
from bareum_ai.rma import request as R
from bareum_ai.rma import targets as T

PILOT = "rma-pilot-1"
CATEGORIES = {"계획보고서": 4, "결과보고서": 4}
PRIORITY_TAGS = ("SEC_RESULT", "SEC_PLAN", "SEC_EFFECT")  # 경계 혼동이 잦은 구간
SKIP_TAGS = ("SEC_ANNEX",)
DOC_UNITS = (15, 37)  # 문서 하나에서 고를 판정 단위 범위. 8편 × 37 ≈ 300
TOTAL_UNITS = (250, 300)
MIN_TABLE_ROWS = 30
MIN_HEADINGS = 20
MIN_TOPIC_DOCS = 2
# 행사·교육 주제는 라벨 전이라 제목으로만 후보를 가린다. 실제 주제는 파일럿 라벨이 정한다.
TOPIC_HINT = re.compile(r"축제|행사|교육|연수|설명회|워크숍|세미나|캠프|박람회|공연|컨설팅|챌린지|장터")
MAX_SEEDS = 1000


def title(group_id: str) -> str:
    """분할 manifest의 파일명은 NFD일 수 있어 비교·표시 전에 NFC로 맞춘다."""
    return unicodedata.normalize("NFC", group_id.split("/", 1)[-1])


def _units_by_section(doc: dict, classes: dict) -> dict:
    """section_id → {"units", "table_rows", "headings"} (판정 대상만)."""
    out: dict = {}
    for block in doc["blocks"]:
        cls = classes[block["id"]]["class"]
        if cls not in (T.TARGET, T.TABLE) or not block.get("section_id"):
            continue
        entry = out.setdefault(block["section_id"], {"units": 0, "table_rows": 0, "headings": 0})
        n = T.unit_count(block)
        entry["units"] += n
        entry["table_rows"] += n if cls == T.TABLE else 0
        entry["headings"] += block["kind"] == "heading"
    return out


def choose_sections(doc: dict) -> list[str]:
    """우선 구간을 먼저, 그다음 나머지 구간을 문서 순서로 통째로 담는다. 상한을 넘는 구간은 건너뛴다."""
    tags = sections.tag_document(doc)
    stats = _units_by_section(doc, T.classify(doc, tags))
    order = [s["section_id"] for s in doc.get("sections") or [] if s["section_id"] in stats]
    order = [s for s in order if tags.get(s) not in SKIP_TAGS]
    ranked = [s for s in order if tags.get(s) in PRIORITY_TAGS] + [
        s for s in order if tags.get(s) not in PRIORITY_TAGS
    ]
    chosen, total = [], 0
    for section_id in ranked:
        if total + stats[section_id]["units"] <= DOC_UNITS[1]:
            chosen.append(section_id)
            total += stats[section_id]["units"]
    if not any(tags.get(s) in PRIORITY_TAGS for s in chosen) or total < DOC_UNITS[0]:
        return []
    return [s for s in order if s in chosen]


def _summary(doc: dict, chosen: list[str]) -> dict:
    stats = _units_by_section(doc, T.classify(doc, sections.tag_document(doc)))
    return {k: sum(stats[s][k] for s in chosen) for k in ("units", "table_rows", "headings")}


def select(candidates: dict[str, list[dict]]) -> dict | None:
    """candidates: 분류 → [{"group_id", "path", "doc"}]. 기준을 모두 채우는 첫 seed의 선정 결과."""
    for seed in range(MAX_SEEDS):
        rng = random.Random(seed)
        picked = []
        for category, count in CATEGORIES.items():
            pool = candidates[category][:]
            rng.shuffle(pool)
            for item in pool:
                if sum(p["category"] == category for p in picked) == count:
                    break
                if any(p["group_id"] == item["group_id"] for p in picked):
                    continue
                chosen = choose_sections(item["doc"])
                if chosen:
                    picked.append({**item, "category": category, "sections": chosen, **_summary(item["doc"], chosen)})
        if len(picked) != sum(CATEGORIES.values()):
            continue
        totals = {k: sum(p[k] for p in picked) for k in ("units", "table_rows", "headings")}
        topic_docs = sum(bool(TOPIC_HINT.search(title(p["group_id"]))) for p in picked)
        if (
            TOTAL_UNITS[0] <= totals["units"] <= TOTAL_UNITS[1]
            and totals["table_rows"] >= MIN_TABLE_ROWS
            and totals["headings"] >= MIN_HEADINGS
            and topic_docs >= MIN_TOPIC_DOCS
        ):
            return {"seed": seed, "documents": picked, "totals": {**totals, "topic_hint_docs": topic_docs}}
    return None


def candidates_from(manifest_path: Path, parsed_root: Path) -> dict[str, list[dict]]:
    """Dev 문서. A/B 판본은 한 쌍에서 하나만 고르도록 group_id를 함께 둔다."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    paths = iter(split_files(manifest_path, parsed_root, "dev"))
    for group in manifest["groups"]:
        if group["split"] != "dev":
            continue
        for _ in group["files"]:
            path = next(paths)
            out[group["category"]].append(
                {"group_id": group["group_id"], "path": str(path), "doc": json.loads(path.read_text(encoding="utf-8"))}
            )
    return out


def freeze(selection: dict, work: Path) -> dict:
    """요청을 고정해 저장하고 pilot.json을 쓴다."""
    if (work / "pilot.json").exists():
        raise ValueError("기존 파일럿과 라벨을 덮어쓸 수 없습니다. 새 작업 폴더를 사용하세요.")
    requests_dir = work / "requests"
    requests_dir.mkdir(parents=True, exist_ok=True)
    documents = []
    for item in selection["documents"]:
        doc = item["doc"]
        tags = sections.tag_document(doc)
        chosen = None if item["sections"] is None else set(item["sections"])
        doc_request = R.document_request(doc, tags)
        block_requests = R.block_requests(
            doc, tags, {"document_type": None, "document_topics": None},
            include=None if chosen is None else lambda block: block.get("section_id") in chosen,
        )
        document_id = doc["document"]["document_id"]
        frozen_path, frozen = snapshot.freeze(doc, work / "snapshots")
        (requests_dir / f"{document_id}.json").write_text(
            json.dumps(
                {"document_request": doc_request, "block_requests": block_requests},
                ensure_ascii=False, indent=1,
            ),
            encoding="utf-8",
        )
        documents.append(
            {
                "document_id": document_id,
                "group_id": item["group_id"],
                "title": title(item["group_id"]),
                "category": item["category"],
                "path": str(frozen_path),
                "original_path": item["path"],
                "parsed_sha256": snapshot.content_hash(doc),
                "snapshot": frozen,
                "sections": item["sections"],
                "units": item["units"],
                "table_rows": item["table_rows"],
                "headings": item["headings"],
            }
        )
    pilot = {
        "schema_version": PILOT,
        "created_at": datetime.now(UTC).isoformat(),
        "seed": selection["seed"],
        "criteria": {
            "categories": CATEGORIES,
            "priority_tags": PRIORITY_TAGS,
            "doc_units": DOC_UNITS,
            "total_units": TOTAL_UNITS,
            "min_table_rows": MIN_TABLE_ROWS,
            "min_headings": MIN_HEADINGS,
            "min_topic_hint_docs": MIN_TOPIC_DOCS,
        },
        "specs": R.spec_hashes(),
        "totals": selection["totals"],
        "documents": documents,
    }
    (work / "pilot.json").write_text(json.dumps(pilot, ensure_ascii=False, indent=1), encoding="utf-8")
    return pilot


def whole_documents(paths: list[Path]) -> dict:
    """선정 없이 지정한 문서의 모든 구간을 대상으로 한다. 모델 라벨 검토용 작업 폴더에 쓴다."""
    documents = []
    for path in paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        tags = sections.tag_document(doc)
        stats = _units_by_section(doc, T.classify(doc, tags))
        documents.append(
            {"doc": doc, "group_id": f"{path.parent.name}/{path.stem}", "category": path.parent.name,
             "path": str(path), "sections": None,
             **{k: sum(v[k] for v in stats.values()) for k in ("units", "table_rows", "headings")}}
        )
    totals = {k: sum(d[k] for d in documents) for k in ("units", "table_rows", "headings")}
    return {"seed": None, "documents": documents, "totals": {**totals, "topic_hint_docs": None}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bareum_ai.dataset.rma.pilot", description="RMA 파일럿 선정")
    parser.add_argument("--documents", type=Path, nargs="+", help="선정 없이 이 문서들의 모든 구간으로 작업 폴더를 만든다(검토용)")
    parser.add_argument("--parsed-root", type=Path)
    parser.add_argument("--manifest", type=Path, default=Path("datasets/splits/manifest.json"))
    parser.add_argument("--work", type=Path, required=True, help="파일럿 작업 폴더. 저장소 밖을 권장한다")
    args = parser.parse_args(argv)
    if (args.work / "pilot.json").exists():
        parser.error(f"이미 파일럿이 있습니다: {args.work / 'pilot.json'}")
    if args.documents:
        selection = whole_documents(args.documents)
    elif not args.parsed_root:
        parser.error("--parsed-root 또는 --documents가 필요합니다")
    else:
        selection = select(candidates_from(args.manifest, args.parsed_root))
    if selection is None:
        print("기준을 모두 채우는 선정을 찾지 못했습니다", flush=True)
        return 1
    pilot = freeze(selection, args.work)
    t = pilot["totals"]
    print(
        f"seed {pilot['seed']}: 문서 {len(pilot['documents'])}편, 판정 단위 {t['units']}, "
        f"표 행 {t['table_rows']}, 구간 제목 {t['headings']}, 행사·교육 후보 {t['topic_hint_docs']}편"
        if pilot["seed"] is not None
        else f"검토용: 문서 {len(pilot['documents'])}편, 판정 단위 {t['units']}, 표 행 {t['table_rows']}"
    )
    for d in pilot["documents"]:
        print(f"  [{d['category']}] {d['title'][:40]} — {d['units']}단위")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
