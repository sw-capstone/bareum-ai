"""평가 지표 계산. 모든 비율은 분자·분모를 함께 돌려주고, 분모가 0이면 N/A(None)다.

문장마다 정답(expected)과 예측(predicted)을 받는다.

- 정답: VIOLATION(위반), COMPLIANT(위반 아님), SHOULD_HOLD(근거가 부족해 보류해야 함)
- 예측: VIOLATION, COMPLIANT, HOLD(판단보류)

산식은 하네스 상세 11장 원문이 로컬에 없어 아래 기준으로 정했다. 오탐율의 분모와 보류율
두 방향의 분모는 노션 원문과 대조해 확정한다.

- 정밀도: 위반으로 낸 문장 중 정답이 위반인 비율 (TP / (TP + FP))
- 재현율: 정답이 위반인 문장 중 위반으로 낸 비율 (TP / (TP + FN)). 보류한 위반은 놓친 것이다.
- F1: 2TP / (2TP + FP + FN)
- 오탐율: 정답이 위반이 아닌 문장(COMPLIANT·SHOULD_HOLD) 중 위반으로 낸 비율 (FP / (FP + TN))
- 보류 적중률: 정답이 SHOULD_HOLD인 문장 중 보류한 비율
- 과잉 보류율: 답할 수 있는 문장(정답이 SHOULD_HOLD가 아닌 문장) 중 보류한 비율
"""

from collections.abc import Iterable
from dataclasses import dataclass

VIOLATION = "violation"
COMPLIANT = "compliant"
SHOULD_HOLD = "should_hold"
HOLD = "hold"

_EXPECTED = {VIOLATION, COMPLIANT, SHOULD_HOLD}
_PREDICTED = {VIOLATION, COMPLIANT, HOLD}


@dataclass(frozen=True)
class Case:
    expected: str
    predicted: str


@dataclass(frozen=True)
class Ratio:
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator


@dataclass(frozen=True)
class Metrics:
    precision: Ratio
    recall: Ratio
    f1: Ratio
    false_positive_rate: Ratio
    hold_hit_rate: Ratio
    over_hold_rate: Ratio


def compute_metrics(cases: Iterable[Case]) -> Metrics:
    tp = fp = fn = tn = 0
    should_hold = should_hold_held = 0
    answerable = answerable_held = 0

    for c in cases:
        if c.expected not in _EXPECTED:
            raise ValueError(f"알 수 없는 정답 라벨: {c.expected!r}")
        if c.predicted not in _PREDICTED:
            raise ValueError(f"알 수 없는 예측 라벨: {c.predicted!r}")

        flagged = c.predicted == VIOLATION
        if c.expected == VIOLATION:
            tp += flagged
            fn += not flagged
        else:
            fp += flagged
            tn += not flagged

        held = c.predicted == HOLD
        if c.expected == SHOULD_HOLD:
            should_hold += 1
            should_hold_held += held
        else:
            answerable += 1
            answerable_held += held

    return Metrics(
        precision=Ratio(tp, tp + fp),
        recall=Ratio(tp, tp + fn),
        f1=Ratio(2 * tp, 2 * tp + fp + fn),
        false_positive_rate=Ratio(fp, fp + tn),
        hold_hit_rate=Ratio(should_hold_held, should_hold),
        over_hold_rate=Ratio(answerable_held, answerable),
    )
