import unittest

from bareum_ai import sections, specs
from bareum_ai.rma import prompt as P
from bareum_ai.rma import request as R
from bareum_ai.rma.validate import Issue
from fixtures import document_response, parsed_doc, response_for, small_chunks


def conforms(instance, schema) -> list[str]:
    """이 스키마가 쓰는 범위(type·enum·properties·required·additionalProperties·items·anyOf)만 확인한다."""
    if "anyOf" in schema:
        return [] if any(not conforms(instance, s) for s in schema["anyOf"]) else ["anyOf 불일치"]
    errors = []
    kinds = schema.get("type")
    kinds = [kinds] if isinstance(kinds, str) else kinds or []
    python = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None)}
    if kinds and not any(
        isinstance(instance, python[k]) and not (k == "integer" and isinstance(instance, bool)) for k in kinds
    ):
        return [f"type {kinds}: {instance!r}"]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"enum: {instance!r}")
    if isinstance(instance, dict) and "properties" in schema:
        missing = set(schema["required"]) - set(instance)
        extra = set(instance) - set(schema["properties"])
        if missing:
            errors.append(f"필드 없음: {missing}")
        if extra and schema.get("additionalProperties") is False:
            errors.append(f"추가 필드: {extra}")
        for key, sub in schema["properties"].items():
            if key in instance:
                errors += [f"{key}.{e}" for e in conforms(instance[key], sub)]
    if isinstance(instance, list) and "items" in schema:
        for i, item in enumerate(instance):
            errors += [f"[{i}].{e}" for e in conforms(item, schema["items"])]
    return errors


def objects(schema):
    if schema.get("type") == "object":
        yield schema
    for value in schema.get("properties", {}).values():
        yield from objects(value)
    for value in schema.get("anyOf", []):
        yield from objects(value)
    if "items" in schema:
        yield from objects(schema["items"])


class SchemaTests(unittest.TestCase):
    def setUp(self):
        small_chunks(self)
        doc = parsed_doc()
        self.tags = sections.tag_document(doc)
        self.doc_request = R.document_request(doc, self.tags)
        self.block_requests = R.block_requests(doc, self.tags, document_response(self.doc_request))

    def test_strict_mode_shape(self):
        # 구조화 출력의 strict 모드는 모든 필드 필수, 추가 필드 금지여야 한다
        for schema in (P.block_response_schema(), P.document_response_schema()):
            for obj in objects(schema):
                with self.subTest(properties=list(obj["properties"])):
                    self.assertEqual(obj["required"], list(obj["properties"]))
                    self.assertIs(obj["additionalProperties"], False)

    def test_valid_fixture_responses_conform(self):
        self.assertEqual(conforms(document_response(self.doc_request), P.document_response_schema()), [])
        for request in self.block_requests:
            with self.subTest(targets=request["target_block_ids"]):
                self.assertEqual(conforms(response_for(request), P.block_response_schema()), [])

    def test_enums_follow_moves_yaml(self):
        moves = specs.load("moves")["moves"]
        annotation = P.block_response_schema()["properties"]["blocks"]["items"]["properties"]["annotations"]["items"]
        self.assertEqual(annotation["properties"]["move"]["enum"], list(moves))
        steps = set(annotation["properties"]["steps"]["items"]["properties"]["step"]["enum"])
        self.assertEqual(steps, {s for m in moves.values() for s in (m.get("steps") or {})})


class PromptTests(unittest.TestCase):
    def test_block_prompt_carries_every_definition(self):
        text = P.block_system_prompt()
        for name, move in specs.load("moves")["moves"].items():
            with self.subTest(move=name):
                self.assertIn(name, text)
                for step in move.get("steps") or {}:
                    self.assertIn(f"`{step}`", text)
        self.assertIn("사업기간", text)  # SPEC 허용 값
        self.assertIn("CONTEXT_MISSING", text)
        self.assertNotIn("{moves}", text)

    def test_document_prompt_carries_outline_rules(self):
        text = P.document_system_prompt()
        self.assertIn("기획 · 상황 · 결과 · 검토 · 기타", text)
        self.assertIn("outline_id", text)
        self.assertNotIn("{outline_rules}", text)

    def test_prompt_hash_is_stable(self):
        self.assertEqual(P.prompt_sha256(), P.prompt_sha256())

    def test_retry_message_carries_previous_response_and_issues(self):
        message = P.user_message({"request_id": "x"}, [Issue("V09", "blocks[0]", "찾지 못함")], "{}")
        self.assertIn("V09 blocks[0]: 찾지 못함", message)
        self.assertIn("# 직전 응답\n{}", message)


if __name__ == "__main__":
    unittest.main()
