#!/bin/bash
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
REGION="us-west-2"
ACCOUNT_ID="557418946771"
ECR_REPO_NAME="soundhub-api"
SERVICE_NAME="soundhub-api"
PORT="8080"
CPU="0.5 vCPU"
MEMORY="1 GB"
IMAGE_TAG="latest"

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
echo "==> Building Docker image (linux/amd64)..."
docker buildx build --platform=linux/amd64 -t "${ECR_URI}:${IMAGE_TAG}" .

echo "==> Pushing image to ECR..."
docker push "${ECR_URI}:${IMAGE_TAG}"

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

# Check if service already exists
EXISTING_ARN=$(aws apprunner list-services \
    --region "$REGION" \
    --query "ServiceSummaryList[?ServiceName=='${SERVICE_NAME}'].ServiceArn | [0]" \
    --output text 2>/dev/null || echo "None")

if [ "$EXISTING_ARN" != "None" ] && [ -n "$EXISTING_ARN" ]; then
    echo "    Service exists, triggering new deployment..."
    aws apprunner start-deployment \
        --service-arn "$EXISTING_ARN" \
        --region "$REGION"
    SERVICE_ARN="$EXISTING_ARN"
else
    echo "    Creating new service..."
    SERVICE_ARN=$(aws apprunner create-service \
        --service-name "$SERVICE_NAME" \
        --source-configuration "{
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
        }" \
        --instance-configuration "{
            \"Cpu\": \"${CPU}\",
            \"Memory\": \"${MEMORY}\",
            \"InstanceRoleArn\": \"${INSTANCE_ROLE_ARN}\"
        }" \
        --health-check-configuration '{
            "Protocol": "HTTP",
            "Path": "/",
            "Interval": 10,
            "Timeout": 5,
            "HealthyThreshold": 1,
            "UnhealthyThreshold": 5
        }' \
        --region "$REGION" \
        --query 'Service.ServiceArn' \
        --output text)
fi

echo "    Service ARN: ${SERVICE_ARN}"

# ── Step 6: Poll until RUNNING ──────────────────────────────────────────────
echo "==> Waiting for service to reach RUNNING status..."

while true; do
    STATUS=$(aws apprunner describe-service \
        --service-arn "$SERVICE_ARN" \
        --region "$REGION" \
        --query 'Service.Status' \
        --output text)

    echo "    Status: ${STATUS}"

    if [ "$STATUS" = "RUNNING" ]; then
        break
    elif [ "$STATUS" = "CREATE_FAILED" ] || [ "$STATUS" = "DELETE_FAILED" ]; then
        echo "ERROR: Service reached ${STATUS} state"
        exit 1
    fi

    sleep 15
done

# Get service URL
SERVICE_URL=$(aws apprunner describe-service \
    --service-arn "$SERVICE_ARN" \
    --region "$REGION" \
    --query 'Service.ServiceUrl' \
    --output text)

echo ""
echo "==> Deployment complete!"
echo "    URL: https://${SERVICE_URL}/"
echo "    Test: curl https://${SERVICE_URL}/owl/latest/recordings/3/detections?limit=5&sort=confidence&direction=desc"
