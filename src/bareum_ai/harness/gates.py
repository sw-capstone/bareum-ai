"""judge() 검증 루프의 관문 ⓪·②·③ 판단 로직.

모델 호출·재시도·trace와 무관한 순수 함수만 둔다. 관문 ① 스키마 검사는 스키마 소유가
정해진 뒤 추가한다. 각 check_* 는 통과하면 None, 막으면 Decision을 돌려준다.

관문 ⓪은 모델 호출 전에 check_confidence로 확인하고, 막히면 호출 없이 withheld로 보류
판정을 만든다. ②·③은 호출 후 run_gates로 확인하고 apply_decision으로 응답에 반영한다.
"""

from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum

VIOLATION = "위반가능"
WITHHELD = "판단보류"


class Outcome(Enum):
    EXPOSE = "expose"
    HOLD = "hold"
    DISCARD = "discard"


class HoldReason(Enum):
    NO_EVIDENCE = "no_evidence"
    UNLABELED_MOVE = "unlabeled_move"
    LOW_CONFIDENCE = "low_confidence"
    SCHEMA_FAIL = "schema_fail"
    VERIFICATION_DOWNGRADE = "verification_downgrade"
    LIMIT = "limit"


@dataclass(frozen=True)
class Evidence:
    article_id: str


@dataclass(frozen=True)
class Judgment:
    sentence_id: str
    verdict: str
    evidence: tuple[Evidence, ...]
    hold_reason: HoldReason | None = None


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    hold_reason: HoldReason | None = None


def withheld(sentence_id: str, hold_reason: HoldReason) -> Judgment:
    """근거 없는 판단보류 판정을 만든다. 호출 전 보류는 모델 응답 없이 이것만 쓴다."""
    return Judgment(
        sentence_id=sentence_id, verdict=WITHHELD, evidence=(), hold_reason=hold_reason
    )


def check_confidence(
    task: str, score: float | None, min_confidence: float
) -> Decision | None:
    """관문 ⓪: 법령 판정에서 검색 점수가 기준보다 낮으면 호출 없이 판단보류한다."""
    if task != "regulation":
        return None
    if score is None or score < min_confidence:
        return Decision(Outcome.HOLD, HoldReason.LOW_CONFIDENCE)
    return None


def check_article_exists(
    judgment: Judgment, candidates: Collection[str]
) -> Decision | None:
    """관문 ②: 후보 목록에 없는 조문을 인용했으면 이슈를 폐기한다."""
    if any(e.article_id not in candidates for e in judgment.evidence):
        return Decision(Outcome.DISCARD)
    return None


def check_final(judgment: Judgment, candidates: Collection[str]) -> Decision | None:
    """관문 ③: 위반가능인데 후보 안의 근거가 없으면 판단보류로 낮춘다."""
    if judgment.verdict != VIOLATION:
        return None
    if not any(e.article_id in candidates for e in judgment.evidence):
        return Decision(Outcome.HOLD, HoldReason.VERIFICATION_DOWNGRADE)
    return None


def run_gates(judgment: Judgment, candidates: Collection[str]) -> Decision:
    """모델 응답을 ② → ③ 순서로 확인하고, 모두 통과하면 노출한다."""
    return (
        check_article_exists(judgment, candidates)
        or check_final(judgment, candidates)
        or Decision(Outcome.EXPOSE)
    )


def apply_decision(judgment: Judgment, decision: Decision) -> Judgment | None:
    """응답에 실을 판정을 돌려준다. 폐기는 None, 보류는 판단보류로 바꾼다."""
    if decision.outcome is Outcome.EXPOSE:
        return judgment
    if decision.outcome is Outcome.DISCARD:
        return None
    return withheld(judgment.sentence_id, decision.hold_reason)
