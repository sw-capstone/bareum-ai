#!/usr/bin/env bash
set -Eeuo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir "$work/bin"
export PATH="$work/bin:$PATH" RUNNER_TEMP="$work"
export EC2_INSTANCE_ID=i-03d7d975865752a46
digest="sha256:$(printf '%064d' 1)"
export GITHUB_STEP_SUMMARY="$work/summary" MOCK_STATE="$work/state" MOCK_CALLS="$work/calls"
cat > "$work/bin/aws" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' "$*" >> "$MOCK_CALLS"
if [[ "$2" == send-command ]]; then
  [[ "$MODE" != send-fail ]] || exit 1
  while [[ "$1" != --parameters ]]; do shift; done
  jq -e --arg command "$EXPECTED_REMOTE_COMMAND" \
    '.executionTimeout == ["1200"] and .commands == [$command]' <<< "$2" >/dev/null
  echo command-test
  exit
fi
if [[ "$MODE" == denied ]]; then echo AccessDeniedException >&2; exit 1; fi
if [[ "$MODE" == eventual && ! -f "$MOCK_STATE" ]]; then
  touch "$MOCK_STATE"
  echo InvocationDoesNotExist >&2
  exit 1
fi
if [[ "$MODE" == pending && ! -f "$MOCK_STATE" ]]; then
  touch "$MOCK_STATE"
  echo '{"Status":"InProgress"}'
  exit
fi
status=Success
response=0
case "$MODE" in
  failed) status=Failed; response=1 ;;
  cancelled) status=Cancelled; response=1 ;;
  timedout) status=TimedOut; response=1 ;;
  bad-response) response=1 ;;
esac
jq -n --arg status "$status" --argjson response "$response" \
  '{Status:$status,ResponseCode:$response,StandardOutputContent:"test output",StandardErrorContent:""}'
MOCK
cat > "$work/bin/sleep" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
chmod +x "$work/bin/aws" "$work/bin/sleep"
for service in server ai; do
  export DEPLOY_SERVICE="$service"
  export DEPLOY_IMAGE="ghcr.io/sw-capstone/bareum-$service@$digest"
  export EXPECTED_REMOTE_COMMAND="flock --wait 600 /opt/bareum/.actions-deploy.lock bash /opt/bareum/infra/scripts/deploy.sh $service $DEPLOY_IMAGE"
  for mode in success eventual pending failed cancelled timedout bad-response denied send-fail; do
    export MODE="$mode"
    rm -f "$MOCK_STATE" "$MOCK_CALLS"
    result=0
    bash "$source_dir/scripts/deploy-via-ssm.sh" > "$work/output" 2>&1 || result=$?
    case "$mode" in
      success|eventual|pending) [[ "$result" == 0 ]] ;;
      *) [[ "$result" != 0 ]] ;;
    esac
    grep -Fq -- "--instance-ids $EC2_INSTANCE_ID --document-name AWS-RunShellScript" "$MOCK_CALLS"
    printf 'PASS: %s %s\n' "$service" "$mode"
  done
  other_service=server
  if [[ "$service" == server ]]; then other_service=ai; fi
  for rejected_image in 'ghcr.io/other/image:latest' "ghcr.io/sw-capstone/bareum-$other_service@$digest"; do
    rm -f "$MOCK_CALLS"
    if DEPLOY_IMAGE="$rejected_image" bash "$source_dir/scripts/deploy-via-ssm.sh" > "$work/output" 2>&1; then
      printf 'FAIL: %s accepted image %s\n' "$service" "$rejected_image" >&2
      exit 1
    fi
    [[ ! -s "$MOCK_CALLS" ]]
    printf 'PASS: %s rejects %s before AWS calls\n' "$service" "$rejected_image"
  done
done
