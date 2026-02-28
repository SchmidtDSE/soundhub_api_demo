# Deploying Soundhub API to AWS App Runner

## Prerequisites

- AWS CLI configured with appropriate credentials
- Docker with buildx support
- Access to AWS account `557418946771`

## Critical Requirements

| Requirement | Why |
|-------------|-----|
| Port **8080** | App Runner expects 8080; the API must bind to it |
| `--platform=linux/amd64` | App Runner runs x86_64; builds on Apple Silicon fail without this |
| **pixi** (not pip) | `api_dock` depends on conda packages (pyarrow, flask) managed by pixi |

## Quick Deploy (Automated)

```bash
chmod +x deploy.sh
./deploy.sh
```

The script handles ECR setup, Docker build/push, IAM roles, and App Runner service creation. It polls until the service is `RUNNING` and prints the URL.

## Manual Steps

### 1. Create ECR Repository

```bash
aws ecr create-repository --repository-name soundhub-api --region us-west-2
```

### 2. Login to ECR

```bash
aws ecr get-login-password --region us-west-2 | \
    docker login --username AWS --password-stdin \
    557418946771.dkr.ecr.us-west-2.amazonaws.com
```

### 3. Build Docker Image

```bash
docker buildx build --platform=linux/amd64 -t soundhub-api .
```

### 4. Tag and Push

```bash
docker tag soundhub-api:latest 557418946771.dkr.ecr.us-west-2.amazonaws.com/soundhub-api:latest
docker push 557418946771.dkr.ecr.us-west-2.amazonaws.com/soundhub-api:latest
```

### 5. Create IAM Roles

**ECR Access Role** (allows App Runner to pull images):

```bash
aws iam create-role \
    --role-name AppRunnerECRAccessRole \
    --assume-role-policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "build.apprunner.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }'

aws iam attach-role-policy \
    --role-name AppRunnerECRAccessRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
```

**Instance Role** (allows runtime S3 access):

```bash
aws iam create-role \
    --role-name AppRunnerSoundhubInstanceRole \
    --assume-role-policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "tasks.apprunner.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }'

aws iam put-role-policy \
    --role-name AppRunnerSoundhubInstanceRole \
    --policy-name SoundhubS3Access \
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:ListBucket"],
            "Resource": [
                "arn:aws:s3:::soundhub-dev",
                "arn:aws:s3:::soundhub-dev/*"
            ]
        }]
    }'
```

### 6. Create App Runner Service

```bash
ECR_ROLE_ARN=$(aws iam get-role --role-name AppRunnerECRAccessRole --query 'Role.Arn' --output text)
INSTANCE_ROLE_ARN=$(aws iam get-role --role-name AppRunnerSoundhubInstanceRole --query 'Role.Arn' --output text)

aws apprunner create-service \
    --service-name soundhub-api \
    --source-configuration '{
        "ImageRepository": {
            "ImageIdentifier": "557418946771.dkr.ecr.us-west-2.amazonaws.com/soundhub-api:latest",
            "ImageConfiguration": {"Port": "8080"},
            "ImageRepositoryType": "ECR"
        },
        "AutoDeploymentsEnabled": false,
        "AuthenticationConfiguration": {
            "AccessRoleArn": "'$ECR_ROLE_ARN'"
        }
    }' \
    --instance-configuration '{
        "Cpu": "0.5 vCPU",
        "Memory": "1 GB",
        "InstanceRoleArn": "'$INSTANCE_ROLE_ARN'"
    }' \
    --health-check-configuration '{
        "Protocol": "HTTP",
        "Path": "/",
        "Interval": 10,
        "Timeout": 5,
        "HealthyThreshold": 1,
        "UnhealthyThreshold": 5
    }' \
    --region us-west-2
```

### 7. Check Status

```bash
SERVICE_ARN=$(aws apprunner list-services --region us-west-2 \
    --query "ServiceSummaryList[?ServiceName=='soundhub-api'].ServiceArn | [0]" --output text)

aws apprunner describe-service --service-arn $SERVICE_ARN --region us-west-2
```

### 8. Test

```bash
SERVICE_URL=$(aws apprunner describe-service --service-arn $SERVICE_ARN --region us-west-2 \
    --query 'Service.ServiceUrl' --output text)

curl https://$SERVICE_URL/
```

## Updating / Redeploying

After code changes, rebuild and push the image, then trigger a new deployment:

```bash
docker buildx build --platform=linux/amd64 -t 557418946771.dkr.ecr.us-west-2.amazonaws.com/soundhub-api:latest .
docker push 557418946771.dkr.ecr.us-west-2.amazonaws.com/soundhub-api:latest

aws apprunner start-deployment --service-arn $SERVICE_ARN --region us-west-2
```

Or just run `./deploy.sh` again — it detects the existing service and triggers `start-deployment`.

## Service Management

**Pause** (stop instances, keep config, no compute cost):

```bash
aws apprunner pause-service --service-arn $SERVICE_ARN --region us-west-2
```

**Resume**:

```bash
aws apprunner resume-service --service-arn $SERVICE_ARN --region us-west-2
```

**Delete**:

```bash
aws apprunner delete-service --service-arn $SERVICE_ARN --region us-west-2
```

## Troubleshooting

### Health check failing

- Ensure the container listens on port **8080** (not 8000)
- The health check hits `GET /` — confirm `api_dock` serves this endpoint
- Check logs: `aws apprunner list-operations --service-arn $SERVICE_ARN --region us-west-2`

### Platform mismatch (exec format error)

- You must build with `--platform=linux/amd64`
- On Apple Silicon, Docker Desktop must have "Use Rosetta" or buildx configured

### S3 access denied

- Verify the instance role `AppRunnerSoundhubInstanceRole` has the S3 policy attached
- Confirm the bucket name matches (`soundhub-dev`)

### "api-dock: command not found"

- This means pixi didn't install correctly or PATH isn't set
- Check: `docker run --platform=linux/amd64 -it soundhub-api pixi run which api-dock`

### Local Docker test

```bash
docker buildx build --platform=linux/amd64 -t soundhub-api .
docker run --platform=linux/amd64 -p 8080:8080 soundhub-api
curl http://localhost:8080/
```

## Cost Estimate

| Resource | Config | Approximate Cost |
|----------|--------|-----------------|
| App Runner | 0.5 vCPU, 1 GB, provisioned | ~$15-25/month active |
| App Runner (paused) | — | $0 compute |
| ECR | Image storage | < $1/month |
