"""sections.yaml로 파서 출력의 구간에 태그를 붙인다.

파서는 구간의 경계까지만 정한다. 태그는 표제 문자열만 보고 결정론적으로 붙이며,
사전에 없는 표제는 SEC_UNKNOWN으로 남긴다. 문서 유형은 여기서 정하지 않는다.
"""

from functools import cache
import re
import unicodedata

from bareum_ai import specs

UNKNOWN = "SEC_UNKNOWN"
ANNEX = "SEC_ANNEX"
MIN_PREFIX = 3  # 앞부분 일치를 허용할 최소 표기 길이


def norm(text: str) -> str:
    """표제 비교용 정규화. 자간을 벌린 표제('사 업 명')가 많아 공백을 모두 지운다."""
    t = unicodedata.normalize("NFC", text)
    t = re.sub(r"[〈〉<>【】\[\]（）()「」『』｢｣]", " ", t)
    t = re.sub(r"^[\dⅠ-Ⅹ]+\s*[.)]?\s*", "", t)  # 앞머리 번호. 로마자 장 번호 포함
    t = re.sub(r"[:：].*$", "", t)  # '사업명 : 2026년 …' 의 값 부분
    return re.sub(r"\s+", "", t).strip()


@cache
def lexicon() -> dict[str, str]:
    """표제 → 태그. 긴 표기가 먼저 걸리도록 정렬한다."""
    out: dict[str, str] = {}
    for tag, spec in specs.load("sections")["tags"].items():
        for word in spec.get("lexicon") or []:
            out.setdefault(re.sub(r"\s+", "", word), tag)
    return dict(sorted(out.items(), key=lambda kv: -len(kv[0])))


def lookup(head_text: str) -> str:
    """완전일치 → 앞부분 일치 순. 포함 매칭은 문서 제목을 구간으로 잡아 쓰지 않는다."""
    n = norm(head_text)
    if not n:
        return UNKNOWN
    words = lexicon()
    if n in words:
        return words[n]
    for word, tag in words.items():
        if len(word) >= MIN_PREFIX and n.startswith(word):
            return tag
    return UNKNOWN


def tag_document(payload: dict) -> dict[str, str]:
    """section_id → 태그. 파서가 붙임으로 가려낸 구간은 사전을 보지 않는다."""
    kind_of = {b["id"]: b["kind"] for b in payload["blocks"]}
    return {
        section["section_id"]: (
            ANNEX
            if kind_of.get(section["head_block_id"]) == "attachment"
            else lookup(section["head_text"])
        )
        for section in payload.get("sections") or []
    }
