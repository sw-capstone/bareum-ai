"""RMA 파일럿 라벨링·조정 화면과 확정 도구.

    # 라벨링 (라벨러마다 따로 연다. 다른 라벨러의 결과는 보이지 않는다)
    uv run python -m bareum_ai.dataset.rma.review serve --work <작업 폴더> --annotator <이름>

    # 조정 (두 라벨러의 결과를 나란히 보며 조정본을 만든다)
    uv run python -m bareum_ai.dataset.rma.review serve --work <작업 폴더> --annotator adjudicated \\
        --reference <이름1> --reference <이름2>

    # 확정 (검증을 통과한 문서를 rma-annotations-2로 조립)
    uv run python -m bareum_ai.dataset.rma.review finalize --work <작업 폴더> --annotator <이름>

    # 모델 실행 결과를 기준 라벨러(model)로 불러와 검토
    uv run python -m bareum_ai.dataset.rma.review import-run --work <작업 폴더> --run <실행 디렉터리>

serve는 127.0.0.1에서만 열린다. 저장할 때마다 모델 응답과 같은 검증기로 검사하고 결과를 화면에 돌려준다.
작업 폴더 구성: pilot.json, requests/<document_id>.json, labels/<라벨러>/<document_id>.json,
final/<라벨러>/<document_id>.json
"""

import argparse
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import re
import webbrowser

from bareum_ai import sections, specs
from bareum_ai.dataset.parsing import snapshot
from bareum_ai.rma import request as R
from bareum_ai.rma import targets as T
from bareum_ai.rma.assemble import assemble
from bareum_ai.rma.validate import validate_block_response, validate_document_response

LABELS = "rma-human-labels-2"
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


class Workspace:
    def __init__(self, work: Path):
        self.work = work
        self.pilot = json.loads((work / "pilot.json").read_text(encoding="utf-8"))

    def documents(self) -> list[dict]:
        return self.pilot["documents"]

    def meta(self, document_id: str) -> dict:
        return next(d for d in self.documents() if d["document_id"] == document_id)

    def parsed(self, document_id: str) -> dict:
        meta = self.meta(document_id)
        if meta.get("snapshot"):
            return snapshot.load(self.work / "snapshots", meta["snapshot"])
        doc = json.loads(Path(meta["path"]).read_text(encoding="utf-8"))
        if R.sha256(doc) != meta["parsed_sha256"]:
            raise ValueError(f"파서 출력이 파일럿 고정 시점과 다릅니다: {meta['path']}")
        return doc

    def requests(self, document_id: str) -> dict:
        reqs = json.loads((self.work / "requests" / f"{document_id}.json").read_text(encoding="utf-8"))
        doc = self.parsed(document_id)
        hashes = {snapshot.content_hash(doc), R.sha256(doc)}
        if any(r.get("parsed_sha256") not in hashes or r.get("document_id") != document_id
               for r in [reqs["document_request"], *reqs["block_requests"]]):
            raise ValueError("파일럿 요청이 고정 파싱 입력과 다릅니다")
        return reqs

    def labels_path(self, annotator: str, document_id: str) -> Path:
        if not _NAME.match(annotator):
            raise ValueError("라벨러 이름은 영문·숫자·_.- 40자 이내")
        return self.work / "labels" / annotator / f"{document_id}.json"

    def labels(self, annotator: str, document_id: str) -> dict | None:
        path = self.labels_path(annotator, document_id)
        if not path.exists():
            return None
        saved = json.loads(path.read_text(encoding="utf-8"))
        snapshot.validate(self.parsed(document_id), saved)
        return saved


def check(ws: Workspace, document_id: str, saved: dict) -> dict:
    """저장된 라벨을 검증한다. 문서 응답 오류와 블록 응답 오류(블록 id별)를 돌려준다."""
    reqs = ws.requests(document_id)
    doc = ws.parsed(document_id)
    if saved:
        snapshot.validate(doc, saved)
    texts = R.block_texts(doc, sections.tag_document(doc))
    out = {"document": [], "blocks": {}, "valid": True}
    doc_response = saved.get("document_response")
    if doc_response is not None:
        out["document"] = [i.__dict__ for i in validate_document_response(reqs["document_request"], doc_response, texts)]
    else:
        out["document"] = [{"code": "D01", "path": "document", "message": "문서 단위 라벨이 없습니다"}]
    responses = saved.get("block_responses") or {}
    for request in reqs["block_requests"]:
        response = responses.get(request["request_id"])
        issues = (
            validate_block_response(request, response)
            if response is not None
            else []
        )
        if response is None:
            for target in request["targets"]:
                out["blocks"][target["id"]] = [{"code": "V02", "path": "", "message": "아직 라벨이 없습니다"}]
            continue
        for issue in issues:
            match = re.match(r"blocks\[(\d+)\]", issue.path)
            block_id = (
                response["blocks"][int(match.group(1))].get("block_id")
                if match and int(match.group(1)) < len(response.get("blocks", []))
                else "_request"
            )
            out["blocks"].setdefault(block_id, []).append(issue.__dict__)
    out["valid"] = not out["document"] and not out["blocks"]
    return out


def _display(doc: dict, reqs: dict) -> list[dict]:
    """화면에 보일 블록. 제외 블록은 빼고, 판정 대상 여부와 정규화 원문(표는 행)을 담는다."""
    tags = sections.tag_document(doc)
    classes = T.classify(doc, tags)
    targets = {t["id"] for r in reqs["block_requests"] for t in r["targets"]}
    heads = {s["head_block_id"]: tags.get(s["section_id"]) for s in doc.get("sections") or []}
    out = []
    for block in doc["blocks"]:
        cls = classes[block["id"]]
        if cls["class"] == T.EXCLUDED:
            continue
        entry = {
            "id": block["id"],
            "kind": block["kind"],
            "page": block.get("page"),
            "section_id": block.get("section_id"),
            "section_tag": heads.get(block["id"]),
            "target": block["id"] in targets,
            "status": cls["class"],
            "reason": cls["reason"],
        }
        if block["kind"] == "table":
            entry["rows"] = T.table_rows(block)
        else:
            entry["text"] = R.block_text(block)
        out.append(entry)
    return out


def state(ws: Workspace, annotator: str, references: list[str]) -> dict:
    moves = specs.load("moves")["moves"]
    documents = []
    for meta in ws.documents():
        document_id = meta["document_id"]
        reqs = ws.requests(document_id)
        doc = ws.parsed(document_id)
        saved = ws.labels(annotator, document_id) or {}
        documents.append(
            {
                "meta": {**meta, "snapshot": snapshot.manifest(doc)},
                "document_request": {k: reqs["document_request"][k] for k in ("request_id", "outline_ids", "title")},
                "requests": [
                    {"request_id": r["request_id"], "target_ids": [t["id"] for t in r["targets"]]}
                    for r in reqs["block_requests"]
                ],
                "blocks": _display(doc, reqs),
                "saved": saved,
                "check": check(ws, document_id, saved),
                "references": {name: ws.labels(name, document_id) for name in references},
            }
        )
    return {
        "annotator": annotator,
        "references": references,
        "moves": {
            name: {
                "name": m["name"],
                "goal": m.get("goal"),
                "boundary": m.get("boundary"),
                "steps": list(m.get("steps") or {}),
            }
            for name, m in moves.items()
        },
        "spec_fields": moves["SPEC"]["field"]["enum"],
        "undetermined_reasons": list(moves["UNDETERMINED"]["reasons"]),
        "document_types": ["기획", "상황", "결과", "검토", "기타"],
        "document_topics": ["행사", "교육"],
        "documents": documents,
    }


def save(ws: Workspace, annotator: str, body: dict) -> dict:
    document_id = body["document_id"]
    ws.meta(document_id)  # 파일럿 문서인지 확인
    doc = ws.parsed(document_id)
    if body.get("snapshot_id") != snapshot.manifest(doc)["snapshot_id"]:
        raise ValueError("화면의 파싱 스냅샷이 현재 파일럿과 다릅니다. 화면을 다시 불러오세요.")
    saved = {
        "schema_version": LABELS,
        "annotator": annotator,
        "document_id": document_id,
        "updated_at": datetime.now(UTC).isoformat(),
        "document_response": body.get("document_response"),
        "block_responses": body.get("block_responses") or {},
    }
    saved = snapshot.bind(doc, saved)
    saved["span_basis"] = "RMA 요청에 담긴 정규화 텍스트/표 셀"
    path = ws.labels_path(annotator, document_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    return check(ws, document_id, saved)


def finalize(ws: Workspace, annotator: str) -> tuple[list[str], list[str]]:
    """(확정한 문서, 검증을 통과하지 못해 건너뛴 문서)."""
    done, skipped = [], []
    for meta in ws.documents():
        document_id = meta["document_id"]
        saved = ws.labels(annotator, document_id)
        if not saved or not check(ws, document_id, saved)["valid"]:
            skipped.append(document_id)
            continue
        reqs = ws.requests(document_id)
        doc = ws.parsed(document_id)
        result = assemble(
            doc,
            sections.tag_document(doc),
            reqs["document_request"],
            saved["document_response"],
            [(r, saved["block_responses"][r["request_id"]]) for r in reqs["block_requests"]],
            [],
            {"run_id": f"pilot-{annotator}", "labeler": {"type": "human", "id": annotator}},
        )
        result["pilot"] = {"sections": meta["sections"], "labels_updated_at": saved["updated_at"]}
        out = ws.work / "final" / annotator / f"{document_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        done.append(document_id)
    return done, skipped


def import_run(ws: Workspace, run_dir: Path, name: str) -> list[str]:
    """모델 실행 결과(rma-annotations-2)를 라벨링 화면의 기준 라벨러로 불러온다. 불러온 문서 id 목록."""
    imported = []
    for meta in ws.documents():
        source = run_dir / "documents" / meta["document_id"] / "annotations.json"
        if not source.exists():
            continue
        result = json.loads(source.read_text(encoding="utf-8"))
        doc = ws.parsed(meta["document_id"])
        snapshot.validate(doc, result)
        document = {k: v for k, v in result["document"].items() if k != "document_id"}
        saved = {
            "schema_version": LABELS,
            "annotator": name,
            "document_id": meta["document_id"],
            "updated_at": datetime.now(UTC).isoformat(),
            "source": {"run_id": result["run_id"], "labeler": result["labeler"], "failed_units": result["failed_units"]},
            "document_response": document,
            # 화면은 블록 id로만 찾으므로 실행 결과의 블록을 묶음 하나에 담는다
            "block_responses": {result["run_id"]: {"blocks": result["blocks"]}},
        }
        saved = snapshot.bind(doc, saved)
        saved["span_basis"] = result["span_basis"]
        path = ws.labels_path(name, meta["document_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
        imported.append(meta["document_id"])
    return imported


def make_handler(ws: Workspace, annotator: str, references: list[str]):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value, status: int = 200) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json")

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                self._send(200, files(__package__).joinpath("review.html").read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json(state(ws, annotator, references))
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if self.path != "/api/save":
                self._send(404, b"not found", "text/plain")
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self._json(save(ws, annotator, body))
            except (KeyError, StopIteration, ValueError) as error:
                self._json({"error": str(error)}, 400)

        def log_message(self, *args) -> None:
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bareum_ai.dataset.rma.review", description="RMA 파일럿 라벨링")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_p = sub.add_parser("serve")
    serve_p.add_argument("--work", type=Path, required=True)
    serve_p.add_argument("--annotator", required=True)
    serve_p.add_argument("--reference", action="append", default=[], help="조정할 때 나란히 볼 라벨러")
    serve_p.add_argument("--port", type=int, default=8765)
    serve_p.add_argument("--no-browser", action="store_true")
    fin_p = sub.add_parser("finalize")
    fin_p.add_argument("--work", type=Path, required=True)
    fin_p.add_argument("--annotator", required=True)
    imp_p = sub.add_parser("import-run", help="모델 실행 결과를 기준 라벨러로 불러온다")
    imp_p.add_argument("--work", type=Path, required=True)
    imp_p.add_argument("--run", type=Path, required=True, help="실행 디렉터리(manifest.json이 있는 곳)")
    imp_p.add_argument("--name", default="model", help="화면에 보일 이름")
    args = parser.parse_args(argv)
    if args.command == "import-run":
        ws = Workspace(args.work)
        imported = import_run(ws, args.run, args.name)
        print(f"{len(imported)}편을 '{args.name}'(으)로 불러왔습니다. 화면: serve --annotator <이름> --reference {args.name}")
        return 0 if imported else 1

    ws = Workspace(args.work)
    for name in [args.annotator, *getattr(args, "reference", [])]:
        if not _NAME.match(name):
            parser.error(f"라벨러 이름은 영문·숫자·_.- 40자 이내: {name}")
    if args.command == "finalize":
        done, skipped = finalize(ws, args.annotator)
        print(f"확정 {len(done)}편 → {args.work / 'final' / args.annotator}")
        if skipped:
            print(f"검증 미통과로 건너뜀 {len(skipped)}편: {', '.join(skipped)}")
        return 0 if not skipped else 1
    handler = make_handler(ws, args.annotator, args.reference)
    for port in range(args.port, args.port + 20):  # 다른 화면이 포트를 쓰고 있으면 다음 빈 포트를 쓴다
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            break
        except OSError:
            continue
    else:
        parser.error(f"{args.port}~{args.port + 19} 포트가 모두 사용 중입니다. --port로 다른 포트를 지정하세요")
    url = f"http://127.0.0.1:{port}/"
    print(f"라벨링 화면: {url} (라벨러 {args.annotator})")
    if not args.no_browser:
        webbrowser.open(url)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
