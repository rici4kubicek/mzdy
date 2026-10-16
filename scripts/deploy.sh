#!/usr/bin/env bash
# Build the frontend and deploy the whole stack to AWS.
#
#   AWS_PROFILE=payroll ./scripts/deploy.sh [-c senderDomain=firma.cz]
#
# Requires uv, Node.js 22 and the AWS CLI. Extra arguments are passed to `cdk deploy`.
set -euo pipefail
cd "$(dirname "$0")/.."

export CDK_DEFAULT_REGION="${CDK_DEFAULT_REGION:-eu-central-1}"
export AWS_REGION="$CDK_DEFAULT_REGION"

# The JWT signing key is a SecureString created once outside CloudFormation, so
# its value never appears in a template.
if ! aws ssm get-parameter --name /payroll/jwt-secret >/dev/null 2>&1; then
  echo "Creating JWT signing key in SSM Parameter Store"
  aws ssm put-parameter --name /payroll/jwt-secret --type SecureString \
    --value "$(openssl rand -hex 32)" >/dev/null
fi

(cd frontend && npm ci && npm run build)

cd infra
uv sync --frozen
npx --yes aws-cdk@2.1145.0 deploy Payroll --require-approval never "$@"
