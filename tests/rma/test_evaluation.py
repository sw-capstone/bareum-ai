"""RMA 확정 정답 대비 채점 규칙을 검증한다."""

from bareum_ai.dataset.rma.evaluation.models import (
    Annotation, DocumentFields, Judgment, Labels, Pilot, SpecItem, Unit, UnitKey,
)
from bareum_ai.dataset.rma.evaluation.scoring import score
from fixtures import ann


def test_spec_matches_each_gold_item_once_with_unicode_and_whitespace_normalized() -> None:
    key = UnitKey("doc", "block")
    document = DocumentFields(document_type=None, document_topics=None, outline_id=None)
    gold_item = SpecItem(spec_field=None, raw_label="기간", value="가  1")
    prediction = SpecItem(spec_field=None, raw_label="기간", value="가\t1")
    a = Annotation.model_validate(ann("a1", "SPEC", "가"))
    gold = Labels({key: Judgment("ok", (a.model_copy(update={"spec_items": [gold_item]}),))}, {"doc": document})
    predictions = Labels({key: Judgment("ok", (a.model_copy(update={"spec_items": [prediction, prediction]}),))},
                         {"doc": document})
    pilot = Pilot((Unit(key, "block", "가  1"),), {"doc": "hash"}, {}, {})

    report = score(pilot, gold, predictions, ["SPEC"], [])

    assert report["overall"]["spec"] == {
        "matched": 1, "missing": 0, "added": 1, "support": 1,
        "precision": 0.5, "recall": 1, "f1": 2 / 3,
    }


def test_partial_gold_steps_are_kept_separately_from_full_step_scoring() -> None:
    document = DocumentFields(document_type=None, document_topics=None, outline_id=None)
    partial_key = UnitKey("doc", "partial")
    confirmed_key = UnitKey("doc", "confirmed")
    label = ann("a1", "GROUNDS", "조례", steps=[{"step": "ORDINANCE"}], status="determined")
    confirmed = Annotation.model_validate(label)
    partial = Annotation.model_validate({**label, "step_status": "partial", "step_reason": "종류 일부 확인"})
    wrong_move = Annotation.model_validate(ann("a1", "ACTION", "조례"))
    gold = Labels({partial_key: Judgment("ok", (partial,)), confirmed_key: Judgment("ok", (confirmed,))},
                  {"doc": document})
    predictions = Labels({partial_key: Judgment("ok", (confirmed,)), confirmed_key: Judgment("ok", (wrong_move,))},
                         {"doc": document})
    units = (Unit(partial_key, "block", "조례"), Unit(confirmed_key, "block", "조례"))

    report = score(Pilot(units, {"doc": "hash"}, {}, {}), gold, predictions,
                   ["ACTION", "GROUNDS"], [("GROUNDS", "ORDINANCE")])

    overall = report["overall"]
    assert overall["move_scored_units"] == 2
    assert overall["step_scored_units"] == 1
    assert overall["step"]["micro"]["missing"] == 1
    assert overall["known_steps_in_unconfirmed_units"] == {"matched": 1, "missing": 0}
