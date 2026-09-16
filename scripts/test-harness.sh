#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${root}/harness/src"
cd "$root"
python3 -m unittest discover -s harness/tests -p 'test_*.py' -v
