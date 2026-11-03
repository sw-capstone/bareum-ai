import pytest

from bareum_ai.harness.metrics import (
    COMPLIANT,
    HOLD,
    SHOULD_HOLD,
    VIOLATION,
    Case,
    Ratio,
    compute_metrics,
)


def cases(expected: str, predicted: str, n: int) -> list[Case]:
    return [Case(expected=expected, predicted=predicted)] * n


@pytest.fixture
def mixed() -> list[Case]:
    """TP 3, FN 2, FP 3, TN 9. 정답이 보류인 문장 4개, 답할 수 있는 문장 13개."""
    return [
        *cases(VIOLATION, VIOLATION, 3),
        *cases(VIOLATION, COMPLIANT, 1),
        *cases(VIOLATION, HOLD, 1),
        *cases(COMPLIANT, VIOLATION, 2),
        *cases(COMPLIANT, COMPLIANT, 4),
        *cases(COMPLIANT, HOLD, 2),
        *cases(SHOULD_HOLD, HOLD, 2),
        *cases(SHOULD_HOLD, COMPLIANT, 1),
        *cases(SHOULD_HOLD, VIOLATION, 1),
    ]


def test_ratio_value_is_division():
    assert Ratio(3, 6).value == 0.5


def test_ratio_with_zero_denominator_is_not_available():
    r = Ratio(0, 0)
    assert r.value is None
    assert (r.numerator, r.denominator) == (0, 0)


def test_precision(mixed):
    assert compute_metrics(mixed).precision == Ratio(3, 6)


def test_recall_counts_held_violations_as_missed(mixed):
    assert compute_metrics(mixed).recall == Ratio(3, 5)


def test_f1_uses_counts(mixed):
    assert compute_metrics(mixed).f1 == Ratio(6, 11)


def test_false_positive_rate_uses_every_non_violation_as_denominator(mixed):
    assert compute_metrics(mixed).false_positive_rate == Ratio(3, 12)


def test_hold_hit_rate_is_share_of_should_hold_that_was_held(mixed):
    assert compute_metrics(mixed).hold_hit_rate == Ratio(2, 4)


def test_over_hold_rate_is_share_of_answerable_that_was_held(mixed):
    assert compute_metrics(mixed).over_hold_rate == Ratio(3, 13)


def test_ratios_are_not_available_without_matching_cases():
    only_compliant = cases(COMPLIANT, COMPLIANT, 2)
    m = compute_metrics(only_compliant)
    assert m.precision.value is None  # 위반으로 낸 문장이 없다
    assert m.recall.value is None  # 정답이 위반인 문장이 없다
    assert m.f1.value is None
    assert m.hold_hit_rate.value is None  # 정답이 보류인 문장이 없다
    assert m.false_positive_rate == Ratio(0, 2)
    assert m.over_hold_rate == Ratio(0, 2)


def test_empty_input_is_all_not_available():
    m = compute_metrics([])
    assert all(
        r.value is None
        for r in (
            m.precision,
            m.recall,
            m.f1,
            m.false_positive_rate,
            m.hold_hit_rate,
            m.over_hold_rate,
        )
    )


def test_unknown_label_is_rejected():
    with pytest.raises(ValueError):
        compute_metrics([Case(expected="위반", predicted=VIOLATION)])


def test_predicted_should_hold_is_rejected():
    # 정답 라벨(SHOULD_HOLD)은 예측으로 쓸 수 없다. 예측의 보류는 HOLD다.
    with pytest.raises(ValueError):
        compute_metrics([Case(expected=VIOLATION, predicted=SHOULD_HOLD)])
