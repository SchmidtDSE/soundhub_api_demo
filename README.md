# Wildlife Sound Hub API Proxy

## Overview

The [Wildlife Sound Hub API](https://api.dev.wildlifesoundhub.org/docs) is a comprehensive REST API for managing wildlife sound recordings, and the backbone of [Wildlife Sound Hub](https://dev.wildlifesoundhub.org/).

This repo extends the soundhub-api, using [api-dock](https://github.com/SchmidtDSE/api_dock) configuration, to create a versioned soundhub-api with additional models. In particular, adding new "detections" (see `/recordings/{id}/detections/` [here](https://api.dev.wildlifesoundhub.org/docs#/recordings/read_recording_detections_recordings__id__detections__get)).


---

## Launch FastAPI

```bash
# start up api-doc (see: api_dock_config/)
pixi run api_dock start

# sepcifiying host/port
pixi run api-dock start --host 0.0.0.0 --port 8080
```

---

## AWS App Runner Deployment

Deploy the API to AWS App Runner for public access. See [AWS_APPRUNNER_DEPLOYMENT.md](AWS_APPRUNNER_DEPLOYMENT.md) for the full guide. For convience the the [deploy](./deploy.sh) can be used. This script will deploy the api to apprunner, and wait for the confirmation that the deployment was successful.


```bash
./deploy.sh
```

The script is idempotent — it creates ECR, IAM roles, and the App Runner service on first run, and triggers a redeployment on subsequent runs. It builds with `--platform=linux/amd64` and binds to port 8080 (required by App Runner). 

To redeploy after changes simply run `./deploy.sh` again. It detects the existing service and calls `start-deployment`.

### Manage the Service

```bash
# Get the service ARN
SERVICE_ARN=$(aws apprunner list-services --region us-west-2 \
    --query "ServiceSummaryList[?ServiceName=='soundhub-api'].ServiceArn | [0]" --output text)

# Check status
aws apprunner describe-service --service-arn $SERVICE_ARN --region us-west-2

# Pause (stops instances, no compute cost)
aws apprunner pause-service --service-arn $SERVICE_ARN --region us-west-2

# Resume
aws apprunner resume-service --service-arn $SERVICE_ARN --region us-west-2

# Delete entirely
aws apprunner delete-service --service-arn $SERVICE_ARN --region us-west-2
```

### Cost
- **Active** (0.5 vCPU, 1 GB): ~$15-25/month
- **Paused**: $0 compute (ECR storage < $1/month)

---

## Development

**Build and run locally with Docker:**

```bash
# Build local Docker image (requires linux/amd64 platform for pixi compatibility)
docker buildx build --platform=linux/amd64 -t soundhub-api:local .

# Run container locally (with AWS credentials for S3 access)
docker run -p 8080:8080 \
  -e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  -e AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN" \
  -e AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
  soundhub-api:local

# Test
curl http://localhost:8080/
```

**Note:** Even on Apple Silicon Macs, local Docker builds must use `--platform=linux/amd64` because the pixi configuration supports `linux-64` but not `linux-aarch64` for the required conda dependencies.

---

## License

BSD 3-Clause