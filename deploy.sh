#!/bin/bash
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
REGION="us-west-2"
ACCOUNT_ID="557418946771"
ECR_REPO_NAME="soundhub-api"
SERVICE_NAME="soundhub-api"
PORT="8080"
# 1 vCPU / 2 GB: DuckDB queries over the model databases need headroom (a full-table
# COUNT(DISTINCT) once OOM-killed the old 0.5 vCPU / 1 GB instance). App Runner only
# pairs 2 GB with 1 vCPU. Changing these updates the existing service on the next run.
CPU="1 vCPU"
MEMORY="2 GB"
# A unique tag per build (commit + UTC time, "-dirty" with uncommitted changes),
# so App Runner always sees a new image and deploys it. With a fixed tag such as
# `latest`, update-service sees no change and keeps running the old image.
# Override with IMAGE_TAG=... ./deploy.sh
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "nogit")
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then GIT_SHA="${GIT_SHA}-dirty"; fi
IMAGE_TAG="${IMAGE_TAG:-${GIT_SHA}-$(date -u +%Y%m%d%H%M%S)}"

ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO_NAME}"

echo "==> Deploying Soundhub API to AWS App Runner"

# ── Step 1: Create ECR repository (idempotent) ──────────────────────────────
echo "==> Creating ECR repository..."
aws ecr create-repository \
    --repository-name "$ECR_REPO_NAME" \
    --region "$REGION" \
    --image-scanning-configuration scanOnPush=true \
    2>/dev/null || echo "    Repository already exists"

# ── Step 2: ECR login ───────────────────────────────────────────────────────
echo "==> Logging into ECR..."
aws ecr get-login-password --region "$REGION" | \
    docker login --username AWS --password-stdin \
    "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# ── Step 3: Build and push Docker image ─────────────────────────────────────
echo "==> Building Docker image (linux/amd64), tag ${IMAGE_TAG}..."
docker buildx build --platform=linux/amd64 \
    -t "${ECR_URI}:${IMAGE_TAG}" -t "${ECR_URI}:latest" .

echo "==> Pushing image to ECR..."
docker push "${ECR_URI}:${IMAGE_TAG}"
docker push "${ECR_URI}:latest"

# ── Step 4: Create IAM roles (idempotent) ────────────────────────────────────
echo "==> Setting up IAM roles..."

# ECR access role (for AppRunner to pull images)
aws iam create-role \
    --role-name AppRunnerECRAccessRole \
    --assume-role-policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "build.apprunner.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }' 2>/dev/null || echo "    ECR access role already exists"

aws iam attach-role-policy \
    --role-name AppRunnerECRAccessRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess \
    2>/dev/null || true

# Instance role (for S3 access at runtime)
aws iam create-role \
    --role-name AppRunnerSoundhubInstanceRole \
    --assume-role-policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "tasks.apprunner.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }' 2>/dev/null || echo "    Instance role already exists"

aws iam put-role-policy \
    --role-name AppRunnerSoundhubInstanceRole \
    --policy-name SoundhubS3Access \
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:ListBucket"],
            "Resource": [
                "arn:aws:s3:::dse-soundhub",
                "arn:aws:s3:::dse-soundhub/*"
            ]
        }]
    }'

# Get role ARNs
ECR_ROLE_ARN=$(aws iam get-role --role-name AppRunnerECRAccessRole --query 'Role.Arn' --output text)
INSTANCE_ROLE_ARN=$(aws iam get-role --role-name AppRunnerSoundhubInstanceRole --query 'Role.Arn' --output text)

echo "    ECR Role:      ${ECR_ROLE_ARN}"
echo "    Instance Role: ${INSTANCE_ROLE_ARN}"

# ── Step 5: Create or update App Runner service ─────────────────────────────
echo "==> Deploying App Runner service..."

# One definition of the service configuration, used to create the service or to
# update the existing one (so CPU/memory/health-check changes above always apply).
SOURCE_CONFIGURATION="{
    \"ImageRepository\": {
        \"ImageIdentifier\": \"${ECR_URI}:${IMAGE_TAG}\",
        \"ImageConfiguration\": {
            \"Port\": \"${PORT}\"
        },
        \"ImageRepositoryType\": \"ECR\"
    },
    \"AutoDeploymentsEnabled\": false,
    \"AuthenticationConfiguration\": {
        \"AccessRoleArn\": \"${ECR_ROLE_ARN}\"
    }
}"
INSTANCE_CONFIGURATION="{
    \"Cpu\": \"${CPU}\",
    \"Memory\": \"${MEMORY}\",
    \"InstanceRoleArn\": \"${INSTANCE_ROLE_ARN}\"
}"
HEALTH_CHECK_CONFIGURATION='{
    "Protocol": "HTTP",
    "Path": "/",
    "Interval": 10,
    "Timeout": 5,
    "HealthyThreshold": 1,
    "UnhealthyThreshold": 5
}'

# Check if service already exists
EXISTING_ARN=$(aws apprunner list-services \
    --region "$REGION" \
    --query "ServiceSummaryList[?ServiceName=='${SERVICE_NAME}'].ServiceArn | [0]" \
    --output text 2>/dev/null || echo "None")

if [ "$EXISTING_ARN" != "None" ] && [ -n "$EXISTING_ARN" ]; then
    # update-service applies the configuration and, because the image tag is new,
    # deploys the new image in one operation (start-deployment alone would keep
    # the old CPU/memory).
    echo "    Service exists, updating configuration and deploying..."
    read -r SERVICE_ARN OPERATION_ID < <(aws apprunner update-service \
        --service-arn "$EXISTING_ARN" \
        --source-configuration "$SOURCE_CONFIGURATION" \
        --instance-configuration "$INSTANCE_CONFIGURATION" \
        --health-check-configuration "$HEALTH_CHECK_CONFIGURATION" \
        --region "$REGION" \
        --query '[Service.ServiceArn, OperationId]' \
        --output text)
else
    echo "    Creating new service..."
    read -r SERVICE_ARN OPERATION_ID < <(aws apprunner create-service \
        --service-name "$SERVICE_NAME" \
        --source-configuration "$SOURCE_CONFIGURATION" \
        --instance-configuration "$INSTANCE_CONFIGURATION" \
        --health-check-configuration "$HEALTH_CHECK_CONFIGURATION" \
        --region "$REGION" \
        --query '[Service.ServiceArn, OperationId]' \
        --output text)
fi

if [ -z "${SERVICE_ARN:-}" ] || [ -z "${OPERATION_ID:-}" ] || [ "$OPERATION_ID" = "None" ]; then
    echo "ERROR: App Runner didn't start a deployment (see the AWS error above)"
    exit 1
fi
echo "    Service ARN:  ${SERVICE_ARN}"
echo "    Operation ID: ${OPERATION_ID}"

# ── Step 6: Wait for the deployment operation ───────────────────────────────
# Wait for this operation itself: the service status can read RUNNING before the
# operation starts, and again after a failed deployment is rolled back.
echo "==> Waiting for the deployment to finish..."

while true; do
    OPERATION_STATUS=$(aws apprunner list-operations \
        --service-arn "$SERVICE_ARN" \
        --region "$REGION" \
        --query "OperationSummaryList[?Id=='${OPERATION_ID}'].Status | [0]" \
        --output text)

    echo "    Operation: ${OPERATION_STATUS}"

    case "$OPERATION_STATUS" in
        SUCCEEDED)
            break ;;
        FAILED|ROLLBACK_SUCCEEDED|ROLLBACK_FAILED)
            echo "ERROR: deployment ${OPERATION_STATUS}; check the service's logs in the App Runner console"
            exit 1 ;;
    esac

    sleep 15
done

# Get service URL
SERVICE_URL=$(aws apprunner describe-service \
    --service-arn "$SERVICE_ARN" \
    --region "$REGION" \
    --query 'Service.ServiceUrl' \
    --output text)

DEPLOYED_IMAGE=$(aws apprunner describe-service \
    --service-arn "$SERVICE_ARN" \
    --region "$REGION" \
    --query 'Service.SourceConfiguration.ImageRepository.ImageIdentifier' \
    --output text)

echo ""
echo "==> Deployment complete!"
echo "    Image: ${DEPLOYED_IMAGE}"
echo "    URL: https://${SERVICE_URL}/"
echo "    Test: curl \"https://${SERVICE_URL}/owl/latest/recordings/3/detections?limit=5&sort=confidence&direction=desc\""
