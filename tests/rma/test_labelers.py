import io
import json
import unittest
from unittest import mock

from bareum_ai.rma import labelers as L


class OpenAILabelerTests(unittest.TestCase):
    def test_missing_key_is_reported(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(L.LabelerError):
                L.OpenAILabeler()

    def labeler(self):
        with mock.patch.dict("os.environ", {L.API_KEY_ENV: "test-key"}, clear=True):
            return L.OpenAILabeler(reasoning_effort="high")

    def test_request_body_uses_strict_json_schema(self):
        body = self.labeler()._body("지시", "요청", {"type": "object"}, "rma_block_response_1")
        self.assertEqual(body["model"], L.DEFAULT_MODEL)
        self.assertEqual(body["instructions"], "지시")
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertIs(body["text"]["format"]["strict"], True)
        self.assertEqual(body["reasoning"], {"effort": "high"})

    def test_output_text_is_joined(self):
        payload = {
            "status": "completed",
            "output": [
                {"type": "reasoning", "content": []},
                {"type": "message", "content": [{"type": "output_text", "text": '{"a":'}, {"type": "output_text", "text": "1}"}]},
            ],
        }
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps(payload).encode())
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            text = self.labeler()(system="s", user="u", schema={}, request={"schema_version": "rma-block-request-1"})
        self.assertEqual(text, '{"a":1}')
        sent = urlopen.call_args.args[0]
        self.assertEqual(sent.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(sent.headers["Authorization"], "Bearer test-key")

    def test_missing_output_text_is_an_error(self):
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"status": "incomplete", "output": []}')
        with mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(L.LabelerError):
                self.labeler()(system="s", user="u", schema={}, request={"schema_version": "rma-doc-request-1"})


class ReplayLabelerTests(unittest.TestCase):
    def test_attempt_counts_per_request(self):
        seen = []
        labeler = L.ReplayLabeler(lambda request, attempt: seen.append(attempt) or {"ok": attempt})
        for _ in range(2):
            labeler(system="", user="", schema={}, request={"request_id": "a"})
        labeler(system="", user="", schema={}, request={"request_id": "b"})
        self.assertEqual(seen, [1, 2, 1])


if __name__ == "__main__":
    unittest.main()
