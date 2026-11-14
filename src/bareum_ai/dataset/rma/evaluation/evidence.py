"""근거의 원문 참조를 검사하고 사람이 기록한 의미 검수 결과를 집계한다."""

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from bareum_ai.dataset.rma.evaluation.models import (
    EvidenceReview, Judgment, Labels, Pilot, Report, Span, Unit,
)
from bareum_ai.dataset.rma.evaluation.scoring import rate


@dataclass(frozen=True, slots=True)
class Evidence:
    path: str
    span: Span | None


def _annotation_spans(judgment: Judgment) -> Iterator[Evidence]:
    for annotation in judgment.annotations:
        path = f"annotations/{annotation.annotation_id}"
        yield Evidence(f"{path}/evidence_span", annotation.evidence_span)
        for index, step in enumerate(annotation.steps):
            yield Evidence(f"{path}/steps/{index}/evidence_span", step.evidence_span)
        for index, item in enumerate(annotation.spec_items):
            item_path = f"{path}/spec_items/{index}"
            yield Evidence(f"{item_path}/evidence_span", item.evidence_span)
            if item.label_span is not None:
                yield Evidence(f"{item_path}/label_span", item.label_span)


def _cell_reference(unit: Unit, judgment: Judgment, span: Span) -> tuple[str | None, bool]:
    rows, r, c, own = unit.rows, span.row, span.col, unit.key.row
    if rows is None or r is None or c is None or own is None or span.block_id is not None:
        return None, False
    if not 0 <= r < len(rows) or not 0 <= c < len(rows[r]):
        return None, False
    cell = rows[r][c]
    origin = any(candidate.inherited_from == [r, c] for candidate in rows[own])
    valid = cell.inherited_from is None and (r == own or r in judgment.header_rows or origin)
    return cell.text, valid


def _record(
    evidence_id: str, span: Span | None, text: str | None, reference_valid: bool,
    cell_valid: bool | None, review: EvidenceReview | None,
) -> Report:
    quote_valid = span is not None and text is not None and bool(span.quote) and span.quote in text
    position_valid = (
        quote_valid and span is not None and text is not None
        and span.start is not None and span.end is not None
        and 0 <= span.start < span.end <= len(text) and text[span.start:span.end] == span.quote
    )
    return {
        "evidence_id": evidence_id, "span": span.model_dump(mode="json") if span else None, "source": text,
        "reference_valid": reference_valid, "quote_valid": quote_valid, "position_valid": position_valid,
        "cell_reference_valid": cell_valid, "structurally_valid": reference_valid and position_valid,
        "review": {"verdict": review.verdict, "reason": review.reason} if review else None,
    }


def evaluate_evidence(
    pilot: Pilot, labels: Labels, reviews: dict[str, EvidenceReview],
) -> tuple[Report, set[str]]:
    entries: list[Report] = []
    used: set[str] = set()

    def add(
        evidence_id: str, span: Span | None, text: str | None, ref: bool, cell: bool | None,
    ) -> None:
        review = reviews.get(evidence_id)
        if review is not None:
            used.add(evidence_id)
        entries.append(_record(evidence_id, span, text, ref, cell, review))

    for unit in pilot.units:
        judgment = labels.at(unit.key)
        if not unit.eligible or judgment.status != "ok":
            continue
        prefix = f"{unit.key.document_id}/blocks/{unit.key.block_id}"
        if unit.key.row is not None:
            prefix += f"/rows/{unit.key.row}"
        for evidence in _annotation_spans(judgment):
            span = evidence.span
            evidence_id = f"{prefix}/{evidence.path}"
            if unit.kind == "table_row":
                text, valid = _cell_reference(unit, judgment, span) if span else (None, False)
                add(evidence_id, span, text, valid, valid)
            else:
                valid = span is not None and span.row is None and span.col is None and span.block_id is None
                add(evidence_id, span, unit.source, valid, None)

    for doc_id, document in labels.documents.items():
        spans = []
        if document.document_type is not None or document.document_type_evidence is not None:
            spans.append(Evidence("document_type_evidence", document.document_type_evidence))
        spans.extend(Evidence(f"document_topics_evidence/{index}", topic.evidence_span)
                     for index, topic in enumerate(document.document_topics_evidence))
        covered = {topic.topic for topic in document.document_topics_evidence}
        spans.extend(Evidence(f"missing_topic_evidence/{topic}", None)
                     for topic in sorted(set(document.document_topics or []) - covered))
        for evidence in spans:
            span = evidence.span
            text = pilot.texts[doc_id].get(span.block_id or "") if span else None
            valid = span is not None and text is not None and span.row is None and span.col is None
            add(f"{doc_id}/document/{evidence.path}", span, text, valid, None)

    cell_entries = [e for e in entries if e["cell_reference_valid"] is not None]
    verdicts = Counter(reviews[key].verdict for key in used)
    return {
        "quote_validity": rate(sum(e["quote_valid"] is True for e in entries), len(entries)),
        "position_validity": rate(sum(e["position_valid"] is True for e in entries), len(entries)),
        "cell_reference_validity": rate(sum(e["cell_reference_valid"] is True for e in cell_entries), len(cell_entries)),
        "structural_validity": rate(sum(e["structurally_valid"] is True for e in entries), len(entries)),
        "manual_validity": rate(verdicts["valid"], len(used)),
        "manual_review_coverage": rate(len(used), len(entries)),
        "manual_invalid": verdicts["invalid"], "manual_undetermined": verdicts["undetermined"],
        "entries": entries,
    }, used
