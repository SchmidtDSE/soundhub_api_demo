# Demo Soundhub API Configuration

Demo [api-dock](https://github.com/SchmidtDSE/api_dock) configuration for a versioned soundhub-api with additional models.

---

## Quick Start Commands

```bash
# start up api-doc (see: api_dock_config/)
pixi run api_dock start
```

---

## AWS Deployment

Deploy your API to AWS App Runner for public access:

### Prerequisites
- AWS CLI installed and configured with appropriate permissions
- Docker installed locally
- AWS account with ECR and App Runner access

### Deploy to App Runner

1. **Configure the deployment script:**
   ```bash
   # Edit deploy.sh to set your preferred AWS region
   # Default is us-west-2
   ```

2. **Run the deployment:**
   ```bash
   cd api/
   ./deploy.sh
   ```

The script will:
- Create an ECR repository for your Docker image
- Build and push your Docker image to ECR
- Create an App Runner service with auto-scaling
- Provide you with a public HTTPS URL

**Current Deployment:**
- **Service URL**: https://kq2drpcbik.us-west-2.awsapprunner.com
- **Service Name**: soundhub-api
- **Service ARN**: arn:aws:apprunner:us-west-2:557418946771:service/soundhub-api/2e4411161826479c96748354339853a6
- **Region**: us-west-2

### Managing Your Deployment

#### Check Service Status
```bash
aws apprunner describe-service \
    --service-arn arn:aws:apprunner:us-west-2:557418946771:service/soundhub-api/2e4411161826479c96748354339853a6 \
    --region us-west-2
```

#### Stop/Pause Service (saves costs)
```bash
aws apprunner pause-service \
    --service-arn arn:aws:apprunner:us-west-2:557418946771:service/soundhub-api/2e4411161826479c96748354339853a6 \
    --region us-west-2
```

#### Resume Service
```bash
aws apprunner resume-service \
    --service-arn arn:aws:apprunner:us-west-2:557418946771:service/soundhub-api/2e4411161826479c96748354339853a6 \
    --region us-west-2
```

#### Update Service (redeploy latest image)
```bash
# First push new image to ECR
docker build -t soundhub-api .
docker tag soundhub-api:latest 557418946771.dkr.ecr.us-west-2.amazonaws.com/soundhub-api:latest
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin 557418946771.dkr.ecr.us-west-2.amazonaws.com
docker push 557418946771.dkr.ecr.us-west-2.amazonaws.com/soundhub-api:latest

# Then start deployment
aws apprunner start-deployment \
    --service-arn arn:aws:apprunner:us-west-2:557418946771:service/soundhub-api/2e4411161826479c96748354339853a6 \
    --region us-west-2
```

#### Delete Service (removes everything)
```bash
aws apprunner delete-service \
    --service-arn arn:aws:apprunner:us-west-2:557418946771:service/soundhub-api/2e4411161826479c96748354339853a6 \
    --region us-west-2
```

### Cost Estimate
- **Small API (0.25 vCPU, 0.5 GB RAM)**: ~$15-20/month
- **Medium API (0.5 vCPU, 1 GB RAM)**: ~$30-40/month
- **Paused service**: $0/month (only pay for storage)

---

## ENDPOINT TESTS

```bash
# Basic Remote API - Working (local)
curl http://localhost:8000/

# Test deployed API (replace with your App Runner URL)
curl https://your-service-id.us-west-2.awsapprunner.com/
```

---

## License

BSD 3-Clause