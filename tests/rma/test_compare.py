import copy
import json

import pytest

from bareum_ai.dataset.rma import run
from bareum_ai.dataset.rma.compare import compare, units
from bareum_ai.rma.labelers import ReplayLabeler
from fixtures import parsed_doc
from test_runner import correct


def test_signature_preserves_duplicates_but_ignores_order_and_evidence():
    annotations = [{"move": "GROUNDS", "steps": [{"step": "B"}, {"step": "A"}]},
                   {"move": "ACTION", "steps": []}]
    result = {"blocks": [{"block_id": "b", "table": None, "annotations": annotations}]}
    reordered = copy.deepcopy(result)
    reordered["blocks"][0]["annotations"].reverse()
    reordered["blocks"][0]["annotations"][1]["steps"].reverse()
    reordered["blocks"][0]["annotations"][0]["evidence_span"] = {"quote": "different"}
    assert units(result) == units(reordered)
    reordered["blocks"][0]["annotations"].append(annotations[0])
    assert units(result) != units(reordered)


def test_comparison_counts_missing_units_and_rejects_different_conditions(tmp_path):
    source = tmp_path / "doc.json"
    source.write_text(json.dumps(parsed_doc()))
    roots = []
    for side in ("left", "right"):
        manifest = run([source], ReplayLabeler(correct), tmp_path / side)
        roots.append(tmp_path / side / manifest["run_id"])
    result = compare(*roots)
    assert result["agreement"] == 1
    path = roots[1] / "documents/doc_t/annotations.json"
    labels = json.loads(path.read_text())
    labels["blocks"] = labels["blocks"][1:]
    path.write_text(json.dumps(labels))
    changed = compare(*roots)
    assert changed["total"] == result["total"]
    assert changed["matched"] == result["matched"] - 1
    assert changed["documents"][0]["left_only"] == 1
    path = roots[1] / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["prompt_sha256"] = "different"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="실행 조건"):
        compare(*roots)
