#!/usr/bin/env bash

set -euo pipefail

: "${AWS_REGION:?Set AWS_REGION}"
: "${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID}"

FUNC_NAME="cost-optimizer-idle-shutdown"
ROLE_NAME="cost-optimizer-lambda-role"
RULE_NAME="cost-optimizer-nightly"
HERE="$(cd "$(dirname "$0")" && pwd)"

cd "$HERE"
rm -f idle_shutdown.zip
zip -q idle_shutdown.zip idle_shutdown.py

ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name ec2-stop-and-metrics \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ec2:DescribeInstances","ec2:StopInstances","cloudwatch:GetMetricStatistics"],"Resource":"*"}]}'
  echo "Waiting for role propagation"
  sleep 10
fi

if aws lambda get-function --function-name "$FUNC_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNC_NAME" \
    --zip-file fileb://idle_shutdown.zip --region "$AWS_REGION"
else
  aws lambda create-function --function-name "$FUNC_NAME" \
    --runtime python3.12 --handler idle_shutdown.handler \
    --role "$ROLE_ARN" --timeout 120 --memory-size 256 \
    --zip-file fileb://idle_shutdown.zip --region "$AWS_REGION"
fi

aws events put-rule --name "$RULE_NAME" \
  --schedule-expression "cron(0 20 * * ? *)" --region "$AWS_REGION"

aws lambda add-permission --function-name "$FUNC_NAME" \
  --statement-id eventbridge-invoke --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${AWS_REGION}:${AWS_ACCOUNT_ID}:rule/${RULE_NAME}" \
  --region "$AWS_REGION" 2>/dev/null || echo "Invoke permission already present"

aws events put-targets --rule "$RULE_NAME" --region "$AWS_REGION" \
  --targets "Id"="1","Arn"="arn:aws:lambda:${AWS_REGION}:${AWS_ACCOUNT_ID}:function:${FUNC_NAME}"

echo "Deployed $FUNC_NAME with nightly schedule $RULE_NAME"
