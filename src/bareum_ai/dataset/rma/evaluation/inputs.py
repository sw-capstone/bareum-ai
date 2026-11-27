"""파일럿에서 채점 대상을 고정하고 기존 라벨과 실행 산출물을 연결한다."""

import json
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError

from bareum_ai import sections
from bareum_ai.dataset.parsing import snapshot
from bareum_ai.dataset.rma.evaluation.models import (
    AnnotationFile, Block, Cell, DocumentFields, Exclusion, HumanFile, InputError,
    Judgment, LabelList, Labels, Pilot, Row, Run, RunDocument, RunManifest, Unit, UnitKey,
)
from bareum_ai.dataset.rma.review import Workspace
from bareum_ai.rma import request as R
from bareum_ai.rma import targets as T


def read[T: BaseModel](path: Path, model: type[T]) -> T:
    try:
        return model.model_validate_json(path.read_bytes())
    except ValidationError as error:
        raise InputError(f"{path}: {error.error_count()}개 형식 오류 ({error.errors(include_input=False)})") from error


def _identity(value: str) -> str:
    if not value or value in (".", "..") or Path(value).name != value:
        raise InputError(f"잘못된 식별자: {value!r}")
    return value


def _requests(ws: Workspace, doc_id: str) -> list[dict]:
    pinned = ws.requests(doc_id)
    for request in [pinned["document_request"], *pinned["block_requests"]]:
        if request["request_id"] != R.sha256({k: v for k, v in request.items() if k != "request_id"}):
            raise InputError(f"고정 요청 내용과 요청 ID가 다릅니다: {doc_id}/{request['request_id']}")
    return pinned["block_requests"]


def load_pilot(ws: Workspace, exclusions: Path | None) -> Pilot:
    units: dict[UnitKey, Unit] = {}
    hashes: dict[str, str] = {}
    texts: dict[str, dict[str, str]] = {}
    for meta in ws.documents():
        doc_id = _identity(meta["document_id"])
        if doc_id in hashes:
            raise InputError(f"파일럿 문서 중복: {doc_id}")
        doc = ws.parsed(doc_id)
        if doc["document"]["document_id"] != doc_id:
            raise InputError(f"파일럿 문서 ID 불일치: {doc_id}")
        hashes[doc_id] = snapshot.content_hash(doc)
        tags = sections.tag_document(doc)
        texts[doc_id] = R.block_texts(doc, tags)
        for request in _requests(ws, doc_id):
            for target in request["targets"]:
                key = UnitKey(doc_id, target["id"])
                if target["kind"] == "table":
                    rows = TypeAdapter(list[list[Cell]]).validate_python(target["rows"])
                    for index, row in enumerate(rows):
                        row_key = replace(key, row=index)
                        _add_unit(units, Unit(row_key, "table_row", " | ".join(c.text for c in row), rows))
                else:
                    _add_unit(units, Unit(key, "block", target["text"],
                                         empty_allowed=target.get("title_like", target["kind"] == "heading")))

        classes = T.classify(doc, tags)
        for block in doc["blocks"]:
            cls = classes[block["id"]]
            selected = meta["sections"] is None or block.get("section_id") in meta["sections"]
            if cls["class"] != T.UNREADABLE or not selected:
                continue
            rows = block.get("rows") or []
            if rows:
                for index, row in enumerate(rows):
                    key = UnitKey(doc_id, block["id"], index)
                    _add_unit(units, Unit(key, "table_row", " | ".join(c or "" for c in row),
                                         input_unreadable=cls["reason"]))
            else:
                key = UnitKey(doc_id, block["id"])
                _add_unit(units, Unit(key, "table_row", block.get("source_text", ""),
                                     input_unreadable=cls["reason"]))

    if not units:
        raise InputError("파일럿에 평가할 판정 단위가 없습니다")
    if exclusions is not None:
        for item in TypeAdapter(list[Exclusion]).validate_json(exclusions.read_bytes()):
            key = UnitKey(item.document_id, item.block_id, item.row)
            if key not in units or not units[key].eligible or not item.reason.strip():
                raise InputError(f"선정 범위 밖이거나 중복되었거나 사유가 없는 평가 제외: {key}")
            units[key] = replace(units[key], exclusion=item.reason)
    return Pilot(tuple(units.values()), hashes, ws.pilot["specs"], texts)


def _add_unit(units: dict[UnitKey, Unit], unit: Unit) -> None:
    if unit.key in units:
        raise InputError(f"파일럿 판정 단위 중복: {unit.key}")
    units[unit.key] = unit


def _index_blocks(blocks: Iterable[Block], doc_id: str) -> dict[str, Block]:
    indexed: dict[str, Block] = {}
    for block in blocks:
        if block.block_id in indexed:
            raise InputError(f"응답 블록 중복: {doc_id}/{block.block_id}")
        indexed[block.block_id] = block
    return indexed


def _judgment(labels: LabelList) -> Judgment:
    if labels.annotations is None:
        return Judgment("missing", reason="annotation_list_missing")
    moves = {a.annotation_id: a.move for a in labels.annotations}
    if len(moves) != len(labels.annotations):
        raise InputError("판정 단위에 중복 annotation_id가 있습니다")
    if labels.primary_annotation_id is not None and labels.primary_annotation_id not in moves:
        raise InputError(f"주 기능이 없는 annotation을 가리킵니다: {labels.primary_annotation_id}")
    return Judgment("ok", tuple(labels.annotations), moves.get(labels.primary_annotation_id or ""))


def _project(
    units: Iterable[Unit], blocks: dict[str, Block], failures: dict[str, str], *, draft: bool = False,
) -> dict[UnitKey, Judgment]:
    results: dict[UnitKey, Judgment] = {}
    tables: dict[str, dict[int, Judgment]] = {}
    for unit in units:
        key = unit.key
        block = blocks.get(key.block_id)
        if key.block_id in failures:
            results[key] = Judgment("failed", reason=failures[key.block_id])
        elif block is None:
            results[key] = Judgment("missing", reason="block_not_returned")
        elif unit.kind == "block" and block.table is None:
            results[key] = _draft_judgment(block, empty_allowed=unit.empty_allowed) if draft else _judgment(block)
        elif unit.kind == "table_row" and block.table is not None:
            table = block.table
            if table.table_status == "unreadable":
                results[key] = Judgment("unreadable", reason=table.table_reason)
                continue
            if key.block_id not in tables:
                table_rows = {
                    r.row: replace(_row_judgment(r, draft=draft), role=r.role, header_rows=tuple(table.header_rows))
                    for r in table.rows
                }
                if len(table_rows) != len(table.rows):
                    raise InputError(f"응답 표 행 중복: {key.document_id}/{key.block_id}")
                tables[key.block_id] = table_rows
            results[key] = tables[key.block_id].get(key.row, Judgment("missing", reason="row_not_returned"))
        else:
            results[key] = Judgment("missing", reason="unexpected_block_kind")
    return results


def _draft_judgment(labels: LabelList, *, empty_allowed: bool) -> Judgment:
    if (not labels.annotations and not empty_allowed) or any(not a.move for a in labels.annotations or []):
        return Judgment("missing", reason="unfinished_annotation_list")
    return _judgment(labels)


def _row_judgment(row: Row, *, draft: bool) -> Judgment:
    if row.role is None:
        return Judgment("missing", reason="row_role_missing")
    return _draft_judgment(row, empty_allowed=row.role != "body") if draft else _judgment(row)


def load_human(ws: Workspace, pilot: Pilot, name: str) -> Labels:
    _identity(name)
    units: dict[UnitKey, Judgment] = {}
    documents: dict[str, DocumentFields] = {}
    for doc_id in pilot.hashes:
        saved = ws.labels(name, doc_id)
        if saved is None:
            continue
        result = HumanFile.model_validate(saved)
        if result.annotator != name or result.document_id != doc_id:
            raise InputError(f"독립 라벨 파일의 라벨러 또는 문서 ID 불일치: {name}/{doc_id}")
        requests = {
            req["request_id"]: {t["id"] for t in req["targets"]}
            for req in _requests(ws, doc_id)
        }
        for req_id, response in result.block_responses.items():
            if (req_id != response.request_id or req_id not in requests
                    or any(block.block_id not in requests[req_id] for block in response.blocks)):
                raise InputError(f"독립 라벨과 고정 요청이 다릅니다: {name}/{doc_id}/{req_id}")
        blocks = _index_blocks((b for r in result.block_responses.values() for b in r.blocks), doc_id)
        units.update(_project(_document_units(pilot, doc_id), blocks, {}, draft=True))
        if result.document_response is not None:
            documents[doc_id] = result.document_response
    return Labels(units, documents)


def _document_units(pilot: Pilot, doc_id: str) -> list[Unit]:
    return [u for u in pilot.units if u.key.document_id == doc_id and u.input_unreadable is None]


def _assembled(ws: Workspace, path: Path, doc_id: str, run_id: str) -> AnnotationFile:
    payload = json.loads(path.read_bytes())
    result = AnnotationFile.model_validate(payload)
    snapshot.validate(ws.parsed(doc_id), payload)
    if result.run_id != run_id or result.document.document_id != doc_id:
        raise InputError(f"라벨 결과의 실행 또는 문서 ID 불일치: {path}")
    return result


def load_gold(ws: Workspace, pilot: Pilot, name: str) -> Labels:
    _identity(name)
    units: dict[UnitKey, Judgment] = {}
    documents: dict[str, DocumentFields] = {}
    for doc_id in pilot.hashes:
        path = ws.work / "final" / name / f"{doc_id}.json"
        result = _assembled(ws, path, doc_id, f"pilot-{name}")
        if (result.labeler.get("type") != "human" or result.labeler.get("id") != name
                or result.failed_units or any(result.specs.get(k) != v for k, v in pilot.specs.items())):
            raise InputError(f"확정 정답 파일의 라벨러, 실패 상태 또는 명세 불일치: {path}")
        if result.pilot is None or result.pilot.sections != ws.meta(doc_id)["sections"]:
            raise InputError(f"확정 정답의 구간이 고정 파일럿과 다릅니다: {path}")
        doc_units = _document_units(pilot, doc_id)
        units.update(_project(doc_units, _index_blocks(result.blocks, doc_id), {}))
        for unit in doc_units:
            if unit.eligible and units[unit.key].status not in ("ok", "unreadable"):
                raise InputError(f"확정 정답에 선정 단위가 없습니다: {unit.key}")
        documents[doc_id] = result.document
    return Labels(units, documents)


def load_run(ws: Workspace, pilot: Pilot, root: Path) -> Run:
    manifest = read(root / "manifest.json", RunManifest)
    indexed: dict[str, RunDocument] = {}
    for doc in manifest.documents:
        if doc.document_id is None:
            continue  # 중단된 실행의 아직 시작하지 않은 문서
        if doc.document_id in indexed:
            raise InputError(f"실행 문서 중복: {manifest.run_id}/{doc.document_id}")
        indexed[doc.document_id] = doc
    problems = []
    if manifest.specs != pilot.specs:
        problems.append("specs_mismatch")
    for doc_id, digest in pilot.hashes.items():
        meta = indexed.get(doc_id)
        if meta and meta.parsed_sha256 != digest:
            problems.append(f"parsed_input_mismatch:{doc_id}")
    labels = Labels({}, {})
    if problems:
        return Run(manifest, labels, tuple(problems))

    for doc_id in pilot.hashes:
        meta = indexed.get(doc_id)
        path = root / "documents" / doc_id / "annotations.json"
        if meta is None:
            continue  # 선정 목록에 남아 있으며 채점에서 missing으로 센다.
        if meta.status != "completed" or not path.is_file():
            labels.units.update({
                u.key: Judgment("failed", reason=f"document_{meta.status}" if meta.status != "completed"
                                else "annotations_file_missing")
                for u in _document_units(pilot, doc_id)
            })
            continue
        result = _assembled(ws, path, doc_id, manifest.run_id)
        if (result.labeler != manifest.labeler or len(result.failed_units) != meta.failed_units
                or any(result.specs.get(k) != v for k, v in manifest.specs.items())):
            raise InputError(f"결과 파일이 실행 manifest와 다릅니다: {path}")
        failures = {key: ",".join(f.issue_codes) or "final_response_failed"
                    for f in result.failed_units if f.unit == "blocks" for key in f.block_ids}
        labels.units.update(_project(_document_units(pilot, doc_id), _index_blocks(result.blocks, doc_id), failures))
        if all(f.unit != "document" for f in result.failed_units):
            labels.documents[doc_id] = result.document
    return Run(manifest, labels, ())
