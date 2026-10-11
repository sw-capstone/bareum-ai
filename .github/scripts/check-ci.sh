#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
docker run --rm -v "$PWD:/repo:ro" -w /repo rhysd/actionlint:1.7.12
files=()
for directory in .github scripts infra; do
  [[ -d "$directory" ]] || continue
  while IFS= read -r -d '' file; do
    bash -n "$file"
    files+=("$file")
  done < <(find "$directory" -type f -name '*.sh' -print0)
done
docker run --rm -v "$PWD:/repo:ro" -w /repo \
  koalaman/shellcheck:v0.11.0 "${files[@]}"
