# Demo Soundhub API Configuration

Demo [api-dock](https://github.com/SchmidtDSE/api_dock) configuration for a versioned soundhub-api with additional models.

---

## Quick Start Commands

```bash
# start up api-doc (see: api_dock_config/)
pixi run api_dock start
```

---

## AWS App Runner Deployment

Deploy the API to AWS App Runner for public access. See [AWS_APPRUNNER_DEPLOYMENT.md](AWS_APPRUNNER_DEPLOYMENT.md) for the full guide.

### Prerequisites
- AWS CLI configured with access to account `557418946771`
- Docker with buildx support

### Deploy

```bash
./deploy.sh
```

The script is idempotent — it creates ECR, IAM roles, and the App Runner service on first run, and triggers a redeployment on subsequent runs. It builds with `--platform=linux/amd64` and binds to port 8080 (required by App Runner).

### Redeploy After Changes

Just run `./deploy.sh` again. It detects the existing service and calls `start-deployment`.

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

## Endpoint Tests

```bash
# Local
curl http://localhost:8000/

# Local via Docker (port 8080)
curl http://localhost:8080/

# Deployed (replace with your App Runner URL)
curl https://<SERVICE_URL>/
```

---

## License

BSD 3-Clause