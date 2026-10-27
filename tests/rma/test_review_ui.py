"""실제 HTML의 자동 저장·상태 전환·응답 변환을 Node의 기본 테스트 도구로 검증한다."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("script", ["review_autosave.cjs", "review_labels.cjs"])
def test_review_ui(script):
    node = shutil.which("node")
    if node is None:
        pytest.skip("라벨링 화면의 JavaScript 회귀 검증에는 Node.js가 필요합니다")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name(script))],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
