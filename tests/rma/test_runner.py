import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from bareum_ai.dataset.rma import run, split_files
from bareum_ai.rma import request as R
from bareum_ai.rma import runner
from bareum_ai.rma.labelers import ReplayLabeler
from fixtures import ann, document_response, parsed_doc, response_for, small_chunks

RUN = {"run_id": "test", "labeler": {"type": "model", "id": "replay"}}


def correct(request, attempt):
    if request["schema_version"] == "rma-doc-request-1":
        return document_response(request)
    return response_for(request)


class LabelDocumentTests(unittest.TestCase):
    def setUp(self):
        small_chunks(self)
        self.log = []

    def label(self, respond):
        return runner.label_document(parsed_doc(), ReplayLabeler(respond), RUN, self.log.append)

    def test_all_valid(self):
        result = self.label(correct)
        self.assertEqual(result["failed_units"], [])
        self.assertEqual(len(self.log), 4)  # 문서 요청 1 + 블록 요청 3
        self.assertIn("b3", [b["block_id"] for b in result["blocks"]])

    def test_retry_fixes_invalid_json(self):
        def respond(request, attempt):
            return "not json" if attempt == 1 else correct(request, attempt)

        result = self.label(respond)
        self.assertEqual(result["failed_units"], [])
        self.assertEqual(self.log[0]["issues"][0]["code"], "V00")
        self.assertEqual(self.log[1]["attempt"], 2)

    def test_retries_are_bounded_and_failure_is_recorded(self):
        def respond(request, attempt):
            response = correct(request, attempt)
            if "b9" in request.get("target_block_ids", []):
                response["blocks"][0]["annotations"][0]["evidence_span"]["quote"] = "없는 구절"
            return response

        result = self.label(respond)
        failed = result["failed_units"]
        self.assertEqual([(f["unit"], f["block_ids"], f["issue_codes"]) for f in failed], [("blocks", ["b9"], ["V09"])])
        self.assertEqual(sum(r["request_id"] == failed[0]["request_id"] for r in self.log), runner.MAX_RETRIES + 1)
        self.assertNotIn("b9", [b["block_id"] for b in result["blocks"]])

    def test_document_failure_keeps_blocks_going(self):
        def respond(request, attempt):
            response = correct(request, attempt)
            if request["schema_version"] == "rma-doc-request-1":
                response["document_type"] = "보고"
            return response

        result = self.label(respond)
        self.assertEqual(result["failed_units"][0]["unit"], "document")
        self.assertIsNone(result["document"]["document_type"])
        self.assertIn("b3", [b["block_id"] for b in result["blocks"]])

    def test_context_missing_triggers_full_text_retry(self):
        def respond(request, attempt):
            response = correct(request, attempt)
            if "blocks" in request.get("context", {}) and "b3" in request.get("target_block_ids", []):
                for block in response["blocks"]:
                    if block["table"] is None and block["annotations"]:
                        block["annotations"] = [
                            ann("a1", "UNDETERMINED", block["annotations"][0]["evidence_span"]["quote"],
                                undetermined="CONTEXT_MISSING")
                        ]
                        block["primary_annotation_id"] = None
            return response

        with mock.patch.object(R, "FULL_TEXT_LIMIT", 10):
            result = self.label(respond)
        b3 = next(b for b in result["blocks"] if b["block_id"] == "b3")
        self.assertEqual(b3["annotations"][0]["move"], "GROUNDS")  # 넓힌 문맥의 응답을 썼다
        self.assertEqual(len(result["request_ids"]), 4)


class RunTests(unittest.TestCase):
    def setUp(self):
        small_chunks(self)

    def test_run_writes_manifest_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "doc.json"
            source.write_text(json.dumps(parsed_doc(), ensure_ascii=False), encoding="utf-8")
            manifest = run([source], ReplayLabeler(correct), tmp / "runs")
            run_dir = tmp / "runs" / manifest["run_id"]
            saved = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["documents"][0]["failed_units"], 0)
            self.assertEqual(saved["labeler"]["id"], "replay")
            self.assertTrue(saved["prompt_sha256"])
            annotations = json.loads((run_dir / "documents/doc_t/annotations.json").read_text(encoding="utf-8"))
            self.assertEqual(annotations["run_id"], manifest["run_id"])
            calls = (run_dir / "documents/doc_t/calls.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 4)

    def test_usage_is_summed_per_document(self):
        labeler = ReplayLabeler(correct)
        labeler.last_usage = {"input_tokens": 10, "output_tokens": 3, "output_tokens_details": {"reasoning_tokens": 2}}
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "doc.json"
            source.write_text(json.dumps(parsed_doc(), ensure_ascii=False), encoding="utf-8")
            manifest = run([source], labeler, Path(tmp) / "runs")
        self.assertEqual(
            manifest["documents"][0]["usage"],
            {"input_tokens": 40, "output_tokens": 12, "output_tokens_details.reasoning_tokens": 8},
        )

    def test_split_files_map_pdf_paths_to_parsed_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({"groups": [
                {"split": "dev", "files": [{"path": "보고서/결과보고서/가_A.pdf"}]},
                {"split": "test", "files": [{"path": "보고서/계획보고서/나_A.pdf"}]},
            ]}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(split_files(manifest, Path("/root"), "dev"), [Path("/root/결과보고서/가_A.json")])


if __name__ == "__main__":
    unittest.main()
