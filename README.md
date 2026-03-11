# Wildlife Sound Hub API Proxy

The [Wildlife Sound Hub API](https://api.dev.wildlifesoundhub.org/docs) is a comprehensive REST API for managing wildlife sound recordings, and the backbone of [Wildlife Sound Hub](https://dev.wildlifesoundhub.org/).

This repo extends the soundhub-api, using [api-dock](https://github.com/SchmidtDSE/api_dock) configuration, to create a versioned soundhub-api with additional models. In particular, adding new "detections" (see `/recordings/{id}/detections/` [here](https://api.dev.wildlifesoundhub.org/docs#/recordings/read_recording_detections_recordings__id__detections__get)).


## Endpoints

- **Base API**: https://eshpezgjnn.us-west-2.awsapprunner.com
- **Core SoundHub API**: https://eshpezgjnn.us-west-2.awsapprunner.com/core/latest
- **PNW Owl Detections**: https://eshpezgjnn.us-west-2.awsapprunner.com/owl/latest/recordings/{recording_id}/detections


## PNW Owl Detections

[Pacific Northwest Owl detections](https://github.com/zjruff/Shiny_PNW-Cnet) have been added to the core SoundHub API as described in the [detections endpoint documentation](https://api.dev.wildlifesoundhub.org/docs#/recordings/read_recording_detections_recordings__id__detections__get).

**Endpoint**: `GET /recordings/{recording_id}/detections`

**Query Parameters**:
- `confidence` - Filter by minimum confidence level
- `scientific_name` - Filter by scientific name (case-insensitive)
- `common_name` - Filter by common name (case-insensitive)
- `rank` - Filter by detection rank
- `start_time` - Filter by minimum start time
- `end_time` - Filter by maximum end time
- `sort` - Sort field for results
- `direction` - Sort direction (ASC/DESC, default: ASC)
- `offset` - Number of results to skip
- `limit` - Maximum number of results to return

**Example**:

```bash
 curl "https://eshpezgjnn.us-west-2.awsapprunner.com/owl/latest/recordings/3/detections?limit=5&sort=confidence&direction=desc"
 ```

**Returns**: Array of detection objects

```json
[
  {
    "id": "string - Unique detection identifier",
    "common_name": "string - Common name of detected species",
    "scientific_name": "string - Scientific name of detected species",
    "class": "string - Species classification code used by PNW OWL",
    "confidence": "float - Detection confidence score (0-1)",
    "rank": "integer - Detection rank/priority",
    "start_time": "float - Detection start time in seconds",
    "end_time": "float - Detection end time in seconds",
    "recording_id": "integer - ID of the source recording"
  }
]
```

---

# Development

For local development, a fastapi instance  can be launched directly using [api-dock](https://github.com/SchmidtDSE/api_dock)),

```bash
pixi run api_dock start --port 8080
```

or using docker

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
curl "http://localhost:8080/owl/latest/recordings/123/detections?limit=10"
```

After changes are made, the api can be (re)deployed using AWS App Runner.

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

## License

BSD 3-Clause