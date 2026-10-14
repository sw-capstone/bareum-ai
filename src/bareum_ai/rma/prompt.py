"""moves.yaml에서 프롬프트와 응답 JSON 스키마를 만든다.

정의를 손으로 옮겨 적지 않으므로 moves.yaml이 바뀌면 프롬프트와 그 해시도 바뀐다.
"""

from importlib.resources import files
import json

from bareum_ai import specs
from bareum_ai.rma.request import sha256
from bareum_ai.rma.validate import DOCUMENT_TOPICS, DOCUMENT_TYPES, Issue


def _template(name: str) -> str:
    return files(__package__).joinpath("prompts", f"{name}.md").read_text(encoding="utf-8")


def _fill(template: str, values: dict[str, str]) -> str:
    # 템플릿에 r{행 번호} 같은 중괄호가 있어 str.format 대신 이름을 하나씩 바꾼다
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def _lines(value) -> str:
    if isinstance(value, dict):
        return "\n".join(f"- {k}: {_flat(v)}" for k, v in value.items())
    return "\n".join(f"- {_flat(v)}" for v in value)


def _flat(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_flat(v)}" for k, v in value.items())
    if isinstance(value, list):
        return " / ".join(_flat(v) for v in value)
    return " ".join(str(value).split())


# moves.yaml labeling.table 중 라벨러에게 보여 줄 항목과 그 이름. table_status는 요청 생성기가
# 미리 정하는 값(not_targeted)이 섞여 있어 빼고, 라벨러가 쓸 값은 템플릿 출력 규칙에 적는다.
TABLE_ITEMS = {
    "unit": "판정 단위",
    "evidence_unit": "근거 단위",
    "header": "열·행 제목",
    "merged_cells": "병합 셀",
    "row_role": "행 역할",
    "spec": "표의 SPEC",
    "total_row": "합계 행",
}
INCLUDE_EXAMPLES = False  # 카탈로그의 경계·문맥 예시(prompts/examples.md)를 지시문에 붙일지. 비교 실험용


def _table(table: dict) -> str:
    return "\n".join(f"- {label}: {_flat(table[key])}" for key, label in TABLE_ITEMS.items())


def _move(name: str, move: dict) -> str:
    out = [f"## {name} ({move['name']})", f"- 목적: {_flat(move['goal'])}"]
    if move.get("boundary"):
        out.append(f"- 구분: {_flat(move['boundary'])}")
    if move.get("example"):
        out.append(f"- 예: {move['example']}")
    for step, spec in (move.get("steps") or {}).items():
        out.append(f"- Step `{step}` ({spec['name']}): 예) {spec['example']}")
    if move.get("constraint_review"):
        out.append(f"- 참고: {_flat(move['constraint_review'])}")
    if "field" in move:
        out.append(f"- spec_field 허용 값: {', '.join(move['field']['enum'])}")
        out.extend(f"- {_flat(rule)}" for rule in move["field"]["normalization"])
    return "\n".join(out)


def block_system_prompt(examples: bool | None = None) -> str:
    text = _block_system_prompt()
    if INCLUDE_EXAMPLES if examples is None else examples:
        text += "\n\n" + _template("examples")
    return text


def _block_system_prompt() -> str:
    spec = specs.load("moves")
    moves = spec["moves"]
    auxiliary = {k: moves[k] for k in ("OTHER", "UNDETERMINED")}
    aux_text = "\n\n".join(
        [f"## OTHER\n- {_flat(auxiliary['OTHER']['note'])}"]
        + [
            "## UNDETERMINED\n- "
            + _flat(auxiliary["UNDETERMINED"]["note"])
            + "\n"
            + "\n".join(
                f"- `{reason}`: {_flat(v['정의'])}"
                for reason, v in auxiliary["UNDETERMINED"]["reasons"].items()
            )
        ]
    )
    return _fill(
        _template("block"),
        {
            "rules": _lines(spec["labeling"]["rules"]),
            "table": _table(spec["labeling"]["table"]),
            "moves": "\n\n".join(
                _move(name, move) for name, move in moves.items() if name not in auxiliary
            ),
            "auxiliary": aux_text,
        },
    )


def document_system_prompt() -> str:
    selection = specs.load("sections")["outline_selection"]
    return _fill(
        _template("document"),
        {
            "document_types": " · ".join(DOCUMENT_TYPES),
            "document_topics": " · ".join(DOCUMENT_TOPICS),
            "outline_rules": _lines(selection["rules"]),
        },
    )


def prompt_sha256() -> str:
    return sha256({"block": block_system_prompt(), "document": document_system_prompt()})


def user_message(request: dict, issues: list[Issue] | None = None, previous: str | None = None) -> str:
    """요청 본문. 재요청이면 직전 응답과 검증 오류를 덧붙인다."""
    body = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    if not issues:
        return body
    errors = "\n".join(f"- {i.code} {i.path}: {i.message}" for i in issues)
    return (
        f"{body}\n\n# 직전 응답\n{previous}\n\n# 직전 응답의 검증 오류\n{errors}\n\n"
        "오류를 고쳐 응답 전체를 다시 작성하라."
    )


# --- 응답 JSON 스키마 (구조화 출력용: 모든 필드 필수, 추가 필드 금지) -----------------


def _obj(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


STRING = {"type": "string"}
INT = {"type": "integer"}
N_STRING = {"type": ["string", "null"]}
N_INT = {"type": ["integer", "null"]}

SPAN = _obj({"quote": STRING, "occurrence": N_INT, "row": N_INT, "col": N_INT})
DOC_SPAN = _obj({"block_id": STRING, "quote": STRING, "occurrence": N_INT})


def _annotation_schema() -> dict:
    moves = specs.load("moves")["moves"]
    steps = sorted({s for m in moves.values() for s in (m.get("steps") or {})})
    return _obj(
        {
            "annotation_id": STRING,
            "move": {"type": "string", "enum": list(moves)},
            "evidence_span": SPAN,
            "context_block_ids": {"type": "array", "items": STRING},
            "steps": {
                "type": "array",
                "items": _obj({"step": {"type": "string", "enum": steps}, "evidence_span": SPAN}),
            },
            "step_status": {
                "type": "string",
                "enum": ["determined", "partial", "undetermined", "not_applicable"],
            },
            "step_reason": N_STRING,
            "undetermined_reason": {
                "type": ["string", "null"],
                "enum": [*moves["UNDETERMINED"]["reasons"], None],
            },
            "spec_items": {
                "type": "array",
                "items": _obj(
                    {
                        "spec_field": {
                            "type": ["string", "null"],
                            "enum": [*moves["SPEC"]["field"]["enum"], None],
                        },
                        "raw_label": STRING,
                        "value": STRING,
                        "evidence_span": SPAN,
                        "label_span": _nullable(SPAN),
                        "reason": N_STRING,
                    }
                ),
            },
        }
    )


def block_response_schema() -> dict:
    annotation = _annotation_schema()
    row = _obj(
        {
            "row": INT,
            "role": {"type": "string", "enum": ["header", "body", "empty"]},
            "primary_annotation_id": N_STRING,
            "annotations": {"type": "array", "items": annotation},
        }
    )
    table = _obj(
        {
            "table_status": {"type": "string", "enum": ["labeled", "unreadable"]},
            "table_reason": N_STRING,
            "header_rows": {"type": "array", "items": INT},
            "row_header_cols": {"type": "array", "items": INT},
            "rows": {"type": "array", "items": row},
        }
    )
    block = _obj(
        {
            "block_id": STRING,
            "primary_annotation_id": N_STRING,
            "annotations": {"type": "array", "items": annotation},
            "table": _nullable(table),
        }
    )
    return _obj(
        {
            "schema_version": {"type": "string", "enum": ["rma-block-response-1"]},
            "request_id": STRING,
            "blocks": {"type": "array", "items": block},
        }
    )


def document_response_schema() -> dict:
    return _obj(
        {
            "schema_version": {"type": "string", "enum": ["rma-doc-response-1"]},
            "request_id": STRING,
            "document_type": {"type": ["string", "null"], "enum": [*DOCUMENT_TYPES, None]},
            "document_type_evidence": _nullable(DOC_SPAN),
            "document_type_reason": N_STRING,
            "document_topics": _nullable(
                {"type": "array", "items": {"type": "string", "enum": list(DOCUMENT_TOPICS)}}
            ),
            "document_topics_evidence": {
                "type": "array",
                "items": _obj(
                    {"topic": {"type": "string", "enum": list(DOCUMENT_TOPICS)}, "evidence_span": DOC_SPAN}
                ),
            },
            "document_topics_reason": N_STRING,
            "outline_id": N_STRING,
            "outline_reason": STRING,
        }
    )
