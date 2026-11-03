from bareum_ai.harness.gates import (
    VIOLATION,
    WITHHELD,
    Decision,
    Evidence,
    HoldReason,
    Judgment,
    Outcome,
    apply_decision,
    check_article_exists,
    check_confidence,
    check_final,
    run_gates,
    withheld,
)

CANDIDATES = frozenset({"law1:3", "law1:5"})


def violation(*article_ids: str) -> Judgment:
    return Judgment(
        sentence_id="s1",
        verdict=VIOLATION,
        evidence=tuple(Evidence(article_id=a) for a in article_ids),
    )


# 관문 ⓪ 신뢰도 게이트


def test_confidence_below_threshold_holds_regulation():
    decision = check_confidence("regulation", 0.69, min_confidence=0.7)
    assert decision == Decision(Outcome.HOLD, HoldReason.LOW_CONFIDENCE)


def test_confidence_at_threshold_passes():
    assert check_confidence("regulation", 0.7, min_confidence=0.7) is None


def test_confidence_missing_score_holds_regulation():
    decision = check_confidence("regulation", None, min_confidence=0.7)
    assert decision == Decision(Outcome.HOLD, HoldReason.LOW_CONFIDENCE)


def test_confidence_ignored_for_other_tasks():
    assert check_confidence("expression", None, min_confidence=0.7) is None
    assert check_confidence("revise", 0.0, min_confidence=0.7) is None


def test_confidence_hold_builds_withheld_without_model_response():
    # 관문 ⓪은 호출 전에 막으므로 모델 응답 없이 보류 판정을 만들 수 있어야 한다.
    decision = check_confidence("regulation", 0.1, min_confidence=0.7)
    assert withheld("s1", decision.hold_reason) == Judgment(
        sentence_id="s1",
        verdict=WITHHELD,
        evidence=(),
        hold_reason=HoldReason.LOW_CONFIDENCE,
    )


# 관문 ② 조문 실재 검사


def test_article_in_candidates_passes():
    assert check_article_exists(violation("law1:3"), CANDIDATES) is None


def test_article_outside_candidates_discards():
    decision = check_article_exists(violation("law1:3", "law9:1"), CANDIDATES)
    assert decision == Decision(Outcome.DISCARD)


def test_article_check_passes_when_no_evidence():
    # 근거가 없는 경우의 처리는 관문 ③ 몫이다.
    assert check_article_exists(violation(), CANDIDATES) is None


# 관문 ③ 최종 게이트


def test_violation_without_evidence_is_downgraded():
    decision = check_final(violation(), CANDIDATES)
    assert decision == Decision(Outcome.HOLD, HoldReason.VERIFICATION_DOWNGRADE)


def test_violation_with_candidate_evidence_passes():
    assert check_final(violation("law1:5"), CANDIDATES) is None


def test_violation_with_evidence_outside_candidates_is_downgraded():
    decision = check_final(violation("law9:1"), CANDIDATES)
    assert decision == Decision(Outcome.HOLD, HoldReason.VERIFICATION_DOWNGRADE)


def test_non_violation_without_evidence_passes():
    j = Judgment(sentence_id="s1", verdict="정상", evidence=())
    assert check_final(j, CANDIDATES) is None


# 응답 후 관문 조합


def test_all_gates_pass_exposes():
    decision = run_gates(violation("law1:3"), CANDIDATES)
    assert decision == Decision(Outcome.EXPOSE)


def test_fake_article_is_discarded_before_final_gate():
    decision = run_gates(violation("law9:1"), CANDIDATES)
    assert decision == Decision(Outcome.DISCARD)


def test_violation_without_evidence_holds_with_downgrade():
    decision = run_gates(violation(), CANDIDATES)
    assert decision == Decision(Outcome.HOLD, HoldReason.VERIFICATION_DOWNGRADE)


# 응답 반영: 폐기된 판정은 노출 결과에 없다


def test_apply_exposed_keeps_judgment():
    j = violation("law1:3")
    assert apply_decision(j, Decision(Outcome.EXPOSE)) is j


def test_apply_discard_returns_none():
    assert apply_decision(violation("law9:1"), Decision(Outcome.DISCARD)) is None


def test_apply_hold_replaces_violation_with_withheld_and_reason():
    held = apply_decision(
        violation(), Decision(Outcome.HOLD, HoldReason.VERIFICATION_DOWNGRADE)
    )
    assert held == Judgment(
        sentence_id="s1",
        verdict=WITHHELD,
        evidence=(),
        hold_reason=HoldReason.VERIFICATION_DOWNGRADE,
    )


def test_hold_reason_values_match_contract():
    assert {r.value for r in HoldReason} == {
        "no_evidence",
        "unlabeled_move",
        "low_confidence",
        "schema_fail",
        "verification_downgrade",
        "limit",
    }
