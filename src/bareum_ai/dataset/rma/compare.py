"""두 실행의 Move·Step 조합 일치도를 재계산한다. 정답 대비 성능 지표가 아니다."""

import argparse
import hashlib
import json
from pathlib import Path


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def units(result: dict) -> dict:
    out = {}

    def signature(annotations):
        return tuple(sorted((a["move"], tuple(sorted(s["step"] for s in a["steps"]))) for a in annotations))

    def add(key, annotations):
        if key in out:
            raise ValueError(f"중복 판정 단위: {key}")
        out[key] = signature(annotations)

    for block in result["blocks"]:
        if block["table"] is None:
            add((block["block_id"], None), block["annotations"])
        else:
            for row in block["table"]["rows"]:
                add((block["block_id"], row["row"]), row["annotations"])
    return out


def compare(left: Path, right: Path) -> dict:
    manifests = [read(root / "manifest.json") for root in (left, right)]
    for key in ("labeler", "prompt_sha256", "specs", "policy"):
        if any(key not in m for m in manifests) or manifests[0][key] != manifests[1][key]:
            raise ValueError(f"실행 조건이 다릅니다: {key}")
    indexes = []
    for manifest in manifests:
        if manifest.get("status", "completed") != "completed":
            raise ValueError("완료된 실행만 비교할 수 있습니다")
        index = {}
        for doc in manifest["documents"]:
            if doc.get("status", "completed") != "completed" or doc.get("failed_units") != 0:
                raise ValueError("실패하거나 미완료인 문서가 있습니다")
            if doc["document_id"] in index:
                raise ValueError("문서 ID가 중복됩니다")
            index[doc["document_id"]] = doc
        indexes.append(index)
    if not indexes[0] or indexes[0].keys() != indexes[1].keys():
        raise ValueError("비교할 문서 집합이 다르거나 비어 있습니다")
    documents = []
    for document_id in sorted(indexes[0]):
        a, b = (index[document_id] for index in indexes)
        if not a.get("parsed_sha256") or a["parsed_sha256"] != b.get("parsed_sha256"):
            raise ValueError(f"파싱 입력이 다릅니다: {document_id}")
        paths = [root / "documents" / document_id / "annotations.json" for root in (left, right)]
        results = [read(path) for path in paths]
        for result, manifest in zip(results, manifests):
            if (result["run_id"] != manifest["run_id"] or result["document"]["document_id"] != document_id
                    or result["failed_units"] or result["labeler"] != manifest["labeler"]
                    or any(result["specs"].get(k) != v for k, v in manifest["specs"].items())):
                raise ValueError("결과와 실행 manifest가 다릅니다")
        u, v = map(units, results)
        keys = u.keys() | v.keys()  # 한쪽에만 있는 단위는 불일치로 센다.
        matched = sum(k in u and k in v and u[k] == v[k] for k in keys)
        documents.append({
            "document_id": document_id, "parsed_sha256": a["parsed_sha256"],
            "matched": matched, "total": len(keys), "left_only": len(u.keys() - v.keys()),
            "right_only": len(v.keys() - u.keys()),
            "annotation_sha256": [fingerprint(path) for path in paths],
            "calls_sha256": [fingerprint(root / "documents" / document_id / "calls.jsonl") for root in (left, right)],
            "request_ids_equal": results[0]["request_ids"] == results[1]["request_ids"],
        })
    matched, total = (sum(d[key] for d in documents) for key in ("matched", "total"))
    if not total:
        raise ValueError("비교할 판정 단위가 없습니다")
    return {
        "metric": "rma-move-step-exact-1", "matched": matched, "total": total,
        "agreement": matched / total, "documents": documents,
        "runs": [{**{k: m[k] for k in ("run_id", "created_at", "git_sha", "labeler", "prompt_sha256", "specs", "policy")},
                  "manifest_sha256": fingerprint(root / "manifest.json")}
                 for root, m in zip((left, right), manifests)],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--out", required=True, type=Path, help="비교 결과 JSON. 저장소 밖에 저장한다")
    args = parser.parse_args()
    result = compare(args.left, args.right)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{result['matched']}/{result['total']} = {result['agreement']:.4%} → {args.out}")


if __name__ == "__main__":
    main()
