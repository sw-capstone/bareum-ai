import copy
import json
import tempfile
import unittest
from pathlib import Path

from bareum_ai.dataset.parsing import snapshot as S


def document():
    return {"schema_version": "1.0.0",
            "document": {"document_id": "doc_t", "sha256": "abc", "page_count": 1},
            "parser": {"source_hashes": {"parser.py": "hash"}, "settings": {"wrap": 0.6},
                       "pymupdf_version": "test", "stats": {"elapsed_seconds": 1}},
            "pages": [{"page": 1, "width": 595, "height": 842}],
            "sections": [], "layout_regions": [], "warnings": [],
            "blocks": [{"id": "b1", "page": 1, "bbox": [1, 2, 3, 4],
                        "kind": "para", "text": "그 외", "source_text": "그  외"}]}


class SnapshotTests(unittest.TestCase):
    def test_runtime_stats_do_not_change_identity(self):
        first, second = document(), document()
        second["parser"]["stats"]["elapsed_seconds"] = 999
        self.assertEqual(S.manifest(first), S.manifest(second))

    def test_label_input_changes_invalidate_existing_binding(self):
        doc = document()
        labels = S.bind(doc, {})
        for path, value in [(('blocks', 0, 'text'), '그외'),
                            (('blocks', 0, 'excluded_from_retrieval'), True),
                            (('blocks', 0, 'bbox'), [2, 3, 4, 5]),
                            (('parser', 'settings', 'wrap'), 0.3),
                            (('parser', 'pymupdf_version'), 'different')]:
            with self.subTest(path=path):
                changed = copy.deepcopy(doc)
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(ValueError):
                    S.validate(changed, labels)

    def test_source_anchors_are_verified_separately(self):
        doc = document()
        labels = S.bind(doc, {})
        self.assertEqual(labels['block_sources']['b1']['source_text'], '그  외')
        labels['block_sources']['b1']['source_text'] = '그외'
        with self.assertRaises(ValueError):
            S.validate(doc, labels)

    def test_freeze_is_immutable_and_load_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path, meta = S.freeze(document(), directory)
            original = path.read_bytes()
            again = document()
            again['parser']['stats']['elapsed_seconds'] = 8
            self.assertEqual(S.freeze(again, directory)[0], path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(S.load(directory, meta), document())
            changed = json.loads(path.read_text())
            changed['blocks'][0]['text'] = '다른 문장'
            path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                S.load(directory, meta)
            with self.assertRaises(ValueError):
                S.freeze(document(), directory)

    def test_new_parse_creates_separate_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = document()
            second = document()
            second['blocks'][0]['id'] = 'b2'
            a, _ = S.freeze(first, Path(tmp))
            b, _ = S.freeze(second, Path(tmp))
            self.assertNotEqual(a, b)
            self.assertEqual(json.loads(a.read_text()), first)
