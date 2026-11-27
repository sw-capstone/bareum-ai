"""기존 RMA 산출물의 평가용 입력 모델과 판정 단위."""

from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, JsonValue, NonNegativeInt

type Report = dict[str, JsonValue]
type Pair = tuple[str, str]
type StepStatus = Literal["determined", "partial", "undetermined", "not_applicable"]
type Role = Literal["body", "header", "empty"]
type UnitKind = Literal["block", "table_row"]
type Status = Literal["ok", "missing", "failed", "unreadable"]

UNDETERMINED: Final = "UNDETERMINED"


@dataclass(frozen=True, slots=True)
class InputError(Exception):
    detail: str

    def __str__(self) -> str:
        return self.detail


class InputModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)


class Span(InputModel):
    quote: str
    start: int | None = None
    end: int | None = None
    block_id: str | None = None
    row: int | None = None
    col: int | None = None


class Step(InputModel):
    step: str
    evidence_span: Span | None = None


class SpecItem(InputModel):
    spec_field: str | None
    raw_label: str
    value: str
    evidence_span: Span | None = None
    label_span: Span | None = None


class Annotation(InputModel):
    annotation_id: str
    move: str
    steps: list[Step]
    step_status: StepStatus
    step_reason: str | None = None
    undetermined_reason: str | None = None
    spec_items: list[SpecItem]
    evidence_span: Span | None = None


class LabelList(InputModel):
    annotations: list[Annotation] | None = None
    primary_annotation_id: str | None = None


class Row(LabelList):
    row: NonNegativeInt
    role: Role | None


class Table(InputModel):
    table_status: Literal["labeled", "unreadable", "not_targeted"]
    table_reason: str | None = None
    rows: list[Row]
    header_rows: list[NonNegativeInt]


class Block(LabelList):
    block_id: str
    table: Table | None = None


class TopicEvidence(InputModel):
    topic: str
    evidence_span: Span | None


class DocumentFields(InputModel):
    document_type: str | None
    document_topics: list[str] | None
    outline_id: str | None
    document_type_evidence: Span | None = None
    document_topics_evidence: list[TopicEvidence] = []
    document_type_reason: str | None = None
    document_topics_reason: str | None = None
    outline_reason: str | None = None


class DocumentResult(DocumentFields):
    document_id: str


class Failure(InputModel):
    unit: Literal["document", "blocks"]
    block_ids: list[str]
    issue_codes: list[str]


class GoldSelection(InputModel):
    sections: list[str] | None


class AnnotationFile(InputModel):
    schema_version: Literal["rma-annotations-2"]
    run_id: str
    labeler: Report
    specs: dict[str, str]
    document: DocumentResult
    blocks: list[Block]
    failed_units: list[Failure]
    pilot: GoldSelection | None = None


class BlockResponse(InputModel):
    schema_version: Literal["rma-block-response-1"]
    request_id: str
    blocks: list[Block]


class HumanFile(InputModel):
    schema_version: Literal["rma-human-labels-2"]
    annotator: str
    document_id: str
    document_response: DocumentFields | None = None
    block_responses: dict[str, BlockResponse]


class RunDocument(InputModel):
    document_id: str | None = None
    parsed_sha256: str | None = None
    status: str
    failed_units: NonNegativeInt | None = None


class RunManifest(InputModel):
    run_id: str
    status: str
    labeler: Report
    prompt_sha256: str
    specs: dict[str, str]
    policy: Report
    git_sha: str | None = None
    created_at: str | None = None
    documents: list[RunDocument]


class Exclusion(InputModel):
    document_id: str
    block_id: str
    row: NonNegativeInt | None = None
    reason: str


class EvidenceReview(InputModel):
    run_id: str
    evidence_id: str
    verdict: Literal["valid", "invalid", "undetermined"]
    reason: str


@dataclass(frozen=True, slots=True)
class UnitKey:
    document_id: str
    block_id: str
    row: int | None = None

    def report(self) -> Report:
        return {"document_id": self.document_id, "block_id": self.block_id, "row": self.row}


class Cell(InputModel):
    text: str
    inherited_from: list[NonNegativeInt] | None = None


@dataclass(frozen=True, slots=True)
class Unit:
    key: UnitKey
    kind: UnitKind
    source: str
    rows: list[list[Cell]] | None = None
    input_unreadable: str | None = None
    exclusion: str | None = None
    empty_allowed: bool = False

    @property
    def eligible(self) -> bool:
        return self.input_unreadable is None and self.exclusion is None

    def report(self) -> Report:
        return {**self.key.report(), "kind": self.kind, "source": self.source}


@dataclass(frozen=True, slots=True)
class Judgment:
    status: Status
    annotations: tuple[Annotation, ...] = ()
    primary_move: str | None = None
    role: Role | None = None
    header_rows: tuple[int, ...] = ()
    reason: str | None = None

    @property
    def moves(self) -> set[str]:
        return {a.move for a in self.annotations}

    @property
    def steps(self) -> set[Pair]:
        return {(a.move, s.step) for a in self.annotations for s in a.steps}

    @property
    def step_states(self) -> tuple[tuple[str, StepStatus], ...]:
        return tuple(sorted((a.move, a.step_status) for a in self.annotations))

    @property
    def steps_confirmed(self) -> bool:
        return UNDETERMINED not in self.moves and all(
            a.step_status in ("determined", "not_applicable") for a in self.annotations
        )

    def report(self) -> Report:
        return {
            "status": self.status, "reason": self.reason, "primary_move": self.primary_move,
            "role": self.role, "annotations": [
                {"move": a.move, "steps": sorted(s.step for s in a.steps),
                 "step_status": a.step_status, "step_reason": a.step_reason,
                 "undetermined_reason": a.undetermined_reason,
                 "spec_items": [{"field": i.spec_field, "raw_label": i.raw_label, "value": i.value}
                                for i in a.spec_items]}
                for a in self.annotations
            ],
        }


MISSING: Final = Judgment("missing")


@dataclass(frozen=True, slots=True)
class Labels:
    units: dict[UnitKey, Judgment]
    documents: dict[str, DocumentFields]

    def at(self, key: UnitKey) -> Judgment:
        return self.units.get(key, MISSING)


@dataclass(frozen=True, slots=True)
class Pilot:
    units: tuple[Unit, ...]
    hashes: dict[str, str]
    specs: dict[str, str]
    texts: dict[str, dict[str, str]]


@dataclass(frozen=True, slots=True)
class Run:
    manifest: RunManifest
    labels: Labels
    incompatibilities: tuple[str, ...]
