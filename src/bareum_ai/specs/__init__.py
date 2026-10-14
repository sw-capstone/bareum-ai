"""검사 규칙·섹션 태그·Move 라벨 명세를 읽는다.

명세 파일은 데이터셋 구축, RMA 라벨링, 검사기가 함께 사용한다.
"""

from importlib.resources import files

import yaml

SPEC_NAMES = ("rules", "sections", "moves")


def load(name: str) -> dict:
    """명세 파일 하나를 읽어 dict로 돌려준다."""
    if name not in SPEC_NAMES:
        raise ValueError(f"알 수 없는 명세: {name}")
    text = files(__package__).joinpath(f"{name}.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)
