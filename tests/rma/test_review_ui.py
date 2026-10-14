"""브라우저 저장 로직의 타이머·요청 순서를 Node의 기본 테스트 도구로 검증한다."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_review_autosave():
    node = shutil.which("node")
    if node is None:
        pytest.skip("라벨링 화면의 JavaScript 회귀 검증에는 Node.js가 필요합니다")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("review_autosave.cjs"))],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
