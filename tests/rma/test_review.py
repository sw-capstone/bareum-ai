import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import urllib.request

from bareum_ai import sections
from bareum_ai.dataset.rma import pilot, review
from bareum_ai.rma import request as R
from fixtures import document_response, parsed_doc, response_for, small_chunks


def make_work(tmp: Path) -> Path:
    """가상 문서 하나로 파일럿 작업 폴더를 만든다. 추진배경·추진결과 구간만 고른다."""
    doc = parsed_doc()
    source = tmp / "doc_t.json"
    source.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    selection = {
        "seed": 0,
        "totals": {"units": 8, "table_rows": 3, "headings": 2, "topic_hint_docs": 0},
        "documents": [
            {"doc": doc, "group_id": "결과보고서/가상 문서", "category": "결과보고서", "path": str(source),
             "sections": ["doc_t_s001", "doc_t_s002"], "units": 8, "table_rows": 3, "headings": 2},
        ],
    }
    work = tmp / "work"
    work.mkdir()
    pilot.freeze(selection, work)
    return work


def labels_for(ws: review.Workspace) -> dict:
    reqs = ws.requests("doc_t")
    return {
        "document_id": "doc_t",
        "snapshot_id": ws.meta("doc_t")["snapshot"]["snapshot_id"],
        "document_response": document_response(reqs["document_request"]),
        "block_responses": {r["request_id"]: response_for(r) for r in reqs["block_requests"]},
    }


class PilotTests(unittest.TestCase):
    def test_frozen_input_survives_original_cache_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = review.Workspace(make_work(Path(tmp)))
            original = Path(ws.meta("doc_t")["original_path"])
            original.write_text("{}")
            self.assertEqual(ws.parsed("doc_t"), parsed_doc())
            with self.assertRaisesRegex(ValueError, "덮어쓸"):
                pilot.freeze({"documents": []}, ws.work)

    def test_tampered_frozen_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = review.Workspace(make_work(Path(tmp)))
            path = Path(ws.meta("doc_t")["path"])
            doc = json.loads(path.read_text())
            doc["blocks"][2]["text"] = "다른 내용"
            path.write_text(json.dumps(doc))
            with self.assertRaises(ValueError):
                ws.parsed("doc_t")

    def test_choose_sections_takes_priority_section_and_keeps_order(self):
        with mock.patch.object(pilot, "DOC_UNITS", (5, 37)):
            self.assertEqual(pilot.choose_sections(parsed_doc()), ["doc_t_s001", "doc_t_s002"])

    def test_choose_sections_respects_cap(self):
        # 추진결과(5단위)만 상한 안에 들어간다
        with mock.patch.object(pilot, "DOC_UNITS", (1, 5)):
            self.assertEqual(pilot.choose_sections(parsed_doc()), ["doc_t_s002"])

    def test_title_is_nfc(self):
        import unicodedata
        nfd = unicodedata.normalize("NFD", "결과보고서/축제 결과")
        self.assertEqual(pilot.title(nfd), "축제 결과")
        self.assertTrue(pilot.TOPIC_HINT.search(pilot.title(nfd)))

    def test_freeze_targets_only_selected_sections(self):
        small_chunks(self)
        with tempfile.TemporaryDirectory() as tmp:
            ws = review.Workspace(make_work(Path(tmp)))
            targets = [t["id"] for r in ws.requests("doc_t")["block_requests"] for t in r["targets"]]
            self.assertEqual(sorted(targets), sorted(["b2", "b3", "b4", "b5", "b6", "b12"]))  # 붙임 b9 제외
            self.assertEqual(ws.pilot["specs"], R.spec_hashes())


class ReviewTests(unittest.TestCase):
    def test_old_tab_cannot_save_to_another_snapshot(self):
        body = labels_for(self.ws)
        body["snapshot_id"] = "different"
        with self.assertRaisesRegex(ValueError, "스냅샷"):
            review.save(self.ws, "alice", body)
        self.assertFalse(self.ws.labels_path("alice", "doc_t").exists())

    def setUp(self):
        small_chunks(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = review.Workspace(make_work(Path(self.tmp.name)))

    def test_unlabeled_document_is_reported_per_block(self):
        result = review.check(self.ws, "doc_t", {})
        self.assertFalse(result["valid"])
        self.assertEqual(set(result["blocks"]), {"b2", "b3", "b4", "b5", "b6", "b12"})
        self.assertTrue(result["document"])

    def test_save_validates_and_finalize_assembles(self):
        result = review.save(self.ws, "alice", labels_for(self.ws))
        self.assertTrue(result["valid"], result)
        done, skipped = review.finalize(self.ws, "alice")
        self.assertEqual((done, skipped), (["doc_t"], []))
        final = json.loads((self.ws.work / "final/alice/doc_t.json").read_text(encoding="utf-8"))
        self.assertEqual(final["labeler"], {"type": "human", "id": "alice"})
        self.assertEqual(final["pilot"]["sections"], ["doc_t_s001", "doc_t_s002"])
        self.assertNotIn("b9", [b["block_id"] for b in final["blocks"]])

    def test_issue_is_attributed_to_block(self):
        labels = labels_for(self.ws)
        for response in labels["block_responses"].values():
            for block in response["blocks"]:
                if block["block_id"] == "b3":
                    block["annotations"][0]["evidence_span"]["quote"] = "없는 구절"
        result = review.save(self.ws, "alice", labels)
        self.assertEqual(list(result["blocks"]), ["b3"])
        self.assertEqual(result["blocks"]["b3"][0]["code"], "V09")
        self.assertEqual(review.finalize(self.ws, "alice"), ([], ["doc_t"]))

    def test_annotator_name_is_restricted(self):
        with self.assertRaises(ValueError):
            self.ws.labels_path("../x", "doc_t")

    def test_state_includes_references(self):
        review.save(self.ws, "alice", labels_for(self.ws))
        state = review.state(self.ws, "adjudicated", ["alice", "bob"])
        document = state["documents"][0]
        self.assertIsNotNone(document["references"]["alice"])
        self.assertIsNone(document["references"]["bob"])
        shown = {b["id"]: b for b in document["blocks"]}
        self.assertTrue(shown["b3"]["target"])
        self.assertFalse(shown["b9"]["target"])  # 고르지 않은 구간은 문맥
        self.assertNotIn("b7", shown)  # 제외 블록은 보이지 않는다

    def test_http_server(self):
        server = review.ThreadingHTTPServer(("127.0.0.1", 0), review.make_handler(self.ws, "alice", []))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        self.assertIn(b"RMA", urllib.request.urlopen(base + "/").read())
        state = json.loads(urllib.request.urlopen(base + "/api/state").read())
        self.assertEqual(state["annotator"], "alice")
        body = json.dumps(labels_for(self.ws), ensure_ascii=False).encode()
        saved = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/save", data=body, method="POST")).read())
        self.assertTrue(saved["valid"])


if __name__ == "__main__":
    unittest.main()


class ModelReviewTests(unittest.TestCase):
    def test_model_result_from_other_parse_cannot_be_imported(self):
        from bareum_ai.dataset.parsing import snapshot

        ws = review.Workspace(make_work(self.tmp))
        doc = parsed_doc()
        doc["blocks"][2]["text"] = "다른 문장"
        run = self.tmp / "foreign_run"
        source = run / "documents/doc_t/annotations.json"
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps(snapshot.bind(doc, {})))
        with self.assertRaises(ValueError):
            review.import_run(ws, run, "model")
        self.assertFalse(ws.labels_path("model", "doc_t").exists())

    def setUp(self):
        small_chunks(self)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))

    def test_whole_document_workspace_and_model_import(self):
        from bareum_ai.dataset.rma import run
        from bareum_ai.rma.labelers import ReplayLabeler

        source = self.tmp / "결과보고서" / "doc_t.json"
        source.parent.mkdir()
        source.write_text(json.dumps(parsed_doc(), ensure_ascii=False), encoding="utf-8")
        work = self.tmp / "work"
        work.mkdir()
        pilot.freeze(pilot.whole_documents([source]), work)
        ws = review.Workspace(work)
        targets = [t["id"] for r in ws.requests("doc_t")["block_requests"] for t in r["targets"]]
        self.assertIn("b9", targets)  # 전체 구간이 대상

        def respond(request, attempt):
            if request["schema_version"] == "rma-doc-request-1":
                return document_response(request)
            return response_for(request)

        manifest = run([source], ReplayLabeler(respond, "model-x"), self.tmp / "runs")
        imported = review.import_run(ws, self.tmp / "runs" / manifest["run_id"], "model")
        self.assertEqual(imported, ["doc_t"])
        state = review.state(ws, "reviewer", ["model"])
        model = state["documents"][0]["references"]["model"]
        self.assertEqual(model["document_response"]["document_type"], "결과")
        blocks = {b["block_id"]: b for r in model["block_responses"].values() for b in r["blocks"]}
        self.assertEqual(blocks["b3"]["annotations"][1]["move"], "ACTION")
        self.assertEqual(model["source"]["labeler"]["id"], "model-x")
