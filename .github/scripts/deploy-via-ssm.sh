#!/usr/bin/env bash
set -Eeuo pipefail
: "${EC2_INSTANCE_ID:?Set the EC2_INSTANCE_ID repository variable.}"
: "${DEPLOY_SERVICE:?Deployment service is required.}"
: "${DEPLOY_IMAGE:?Deployment image is required.}"
[[ "$EC2_INSTANCE_ID" =~ ^i-[a-f0-9]{17}$ ]] || { echo 'Invalid EC2 instance ID.' >&2; exit 1; }
[[ "$DEPLOY_SERVICE" == server || "$DEPLOY_SERVICE" == ai ]] || exit 1
[[ "$DEPLOY_IMAGE" =~ ^ghcr\.io/sw-capstone/bareum-${DEPLOY_SERVICE}@sha256:[a-f0-9]{64}$ ]] \
  || { echo 'Expected the published image digest for the target service.' >&2; exit 1; }

# 두 레포의 배포 요청을 같은 EC2 잠금으로 직렬화합니다.
remote_command="flock --wait 600 /opt/bareum/.actions-deploy.lock bash /opt/bareum/infra/scripts/deploy.sh $DEPLOY_SERVICE $DEPLOY_IMAGE"
parameters=$(jq -n --arg command "$remote_command" \
  '{commands: [$command], executionTimeout: ["1200"]}')
command_id=$(aws ssm send-command \
  --instance-ids "$EC2_INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --timeout-seconds 60 \
  --parameters "$parameters" \
  --query Command.CommandId --output text)
printf 'SSM command ID: %s\n' "$command_id"
printf 'SSM command ID: %s\n' "$command_id" >> "$GITHUB_STEP_SUMMARY"

result_file="$RUNNER_TEMP/ssm-result.json"
error_file="$RUNNER_TEMP/ssm-error.txt"
deadline=$((SECONDS + 1500))
while (( SECONDS < deadline )); do
  if aws ssm get-command-invocation --command-id "$command_id" \
    --instance-id "$EC2_INSTANCE_ID" --output json > "$result_file" 2> "$error_file"; then
    status=$(jq -r '.Status' "$result_file")
    case "$status" in
      Pending|InProgress|Delayed|Cancelling) ;;
      Success|Failed|Cancelled|TimedOut)
        jq -r '.StandardOutputContent, .StandardErrorContent' "$result_file"
        printf 'Deployment status: %s\n' "$status" >> "$GITHUB_STEP_SUMMARY"
        [[ "$status" == Success && "$(jq -r '.ResponseCode' "$result_file")" == 0 ]]
        exit "$?"
        ;;
      *) printf 'Unexpected SSM status: %s\n' "$status" >&2; exit 1 ;;
    esac
  elif ! grep -q 'InvocationDoesNotExist' "$error_file"; then
    cat "$error_file" >&2
    exit 1
  fi
  sleep 5
done
printf 'Polling timed out. Check SSM command %s before retrying; it may still be running.\n' "$command_id" >&2
exit 1
