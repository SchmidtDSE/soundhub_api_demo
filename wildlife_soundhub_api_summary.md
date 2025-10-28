# Wildlife Sound Hub API - Architecture Summary

## Overview

The Wildlife Sound Hub API is a comprehensive REST API for managing wildlife sound recordings, species observations, and research project data. It follows a hierarchical structure where **Projects** contain **Deployments**, which contain **Recordings**, which contain **Observations** of species.

**API Base URL**: `https://api.dev.wildlifesoundhub.org`
**Documentation**: OpenAPI 3.1.0 with Swagger UI
**Authentication**: JWE cookies (OAuth2 supported)

---

## Core Data Model Hierarchy

```
Users
  ↓ (many-to-many with roles: owner/editor/viewer)
Projects
  ├── Target Species (many-to-many)
  ├── Taxonomy
  ├── Sensor Layout
  └── Deployments
       ├── Location (GeoPoint)
       ├── ARU Device Info
       ├── Feature Type (habitat)
       ├── Frequency
       └── Recordings
            ├── Audio File Path
            ├── Sample Rate
            ├── Temporal Bounds
            └── Observations (Species Detections)
                 ├── Species
                 ├── Confidence Score
                 ├── Start/End Timestamps
                 └── Rank
```

---

## Resource Breakdown

### 1. **Projects** (`/projects/`)
The top-level organizational unit for research studies.

**Key Fields:**
- `short_name` - Project identifier
- `taxonomy` - Classification system used
- `sensor_layout` - Equipment configuration
- `observed` - Whether species have been observed

**Operations:**
- CRUD operations (GET, POST, PATCH, DELETE)
- List with filters: `short_name`, `taxonomy`, `user`, `observed`
- Aggregate view with geometry: `GET /projects/agg/`

**Sub-resources:**
- `/projects/{id}/observations/` - All observations in project
- `/projects/{id}/deployments/` - All deployments in project
- `/projects/{id}/recordings/` - All recordings in project
- `/projects/{id}/species/{species_id}` - Add/remove target species
- `/projects/{id}/users/` - Manage project members and roles

---

### 2. **Deployments** (`/deployments/`)
Physical locations where recording equipment was deployed.

**Key Fields:**
- `code` - Deployment identifier
- `location` - Geographic Point (GeoJSON)
- `dates` - Start/end timestamps
- `sensor_height` - Equipment height
- `aru` - Autonomous Recording Unit details
- `aru_status` - Device operational status
- `aru_mount` - Mounting type
- `feature_type` - Habitat classification
- `frequency` - Recording frequency
- `trigger_type` - Recording trigger method

**Operations:**
- CRUD operations
- Batch upload: `POST /deployments/upload` (multipart form data)

**Relationships:**
- Belongs to: Project
- Has many: Recordings

---

### 3. **Recordings** (`/recordings/`)
Individual audio files captured during deployments.

**Key Fields:**
- `path` - Audio file storage path
- `sample_rate` - Audio sample rate
- `start_datetime` / `end_datetime` - Temporal bounds
- Project reference
- Deployment reference

**Operations:**
- CRUD operations
- Transcode audio segment: `GET /recordings/{id}/ogg?start={timestamp}&end={timestamp}`
- Get observations: `GET /recordings/{id}/observations/`

**Relationships:**
- Belongs to: Project, Deployment
- Has many: Observations

---

### 4. **Observations** (accessed via `/projects/{id}/observations/` or `/recordings/{id}/observations/`)
Species detections within recordings.

**Key Fields:**
- Recording reference
- Species reference
- `confidence` - Detection confidence score (0-1)
- `start` / `end` - Timestamps within recording
- `rank` - Priority/ranking

**Relationships:**
- Belongs to: Recording
- References: Species

---

### 5. **Users & Access Control** (`/users/`, `/user-projects/`)

**User Operations:**
- CRUD operations
- Get current user: `GET /users/me`

**Access Control:**
- Users are linked to Projects via `UserProject` with roles:
  - `owner` - Full control
  - `editor` - Can modify data
  - `viewer` - Read-only access
- List user projects: `GET /user-projects/?role={role}`

---

### 6. **Revisions** (`/revisions/`)
Change tracking and version control for data modifications.

**Operations:**
- CRUD operations
- Track changes to deployments, recordings, observations

---

## Lookup Tables (Reference Data)

These endpoints provide standardized reference data:

| Endpoint | Purpose |
|----------|---------|
| `/taxa/` | Taxonomic classification system |
| `/species/` | Species directory |
| `/sensorlayouts/` | Recording equipment configurations |
| `/featuretypes/` | Habitat/environment classifications |
| `/frequencies/` | Recording frequency options |
| `/arumount/` | ARU mounting types |
| `/arustatus/` | Device operational status types |

---

## Media & Files

**`/media/{path}`** - Retrieve media files (audio recordings, images, etc.)

---

## API Design Patterns

### 1. **Hierarchical Structure**
```
Project → Deployment → Recording → Observation
```
Each level can be accessed independently or through parent resources.

### 2. **Role-Based Access Control**
User permissions managed through `UserProject` with three role levels (owner/editor/viewer).

### 3. **Spatial Data Support**
Deployments include GeoJSON Point geometries for geographic queries and mapping.

### 4. **Temporal Filtering**
All temporal resources support datetime-based filtering for time-series analysis.

### 5. **Batch Operations**
Upload multiple deployments at once via multipart form data.

### 6. **Audio Processing**
On-demand transcoding of audio segments to OGG format with temporal subsetting.

---

## Entity Relationship Diagram

```
┌──────────────┐
│    Users     │
└──────┬───────┘
       │ (many-to-many via UserProject)
       │ roles: owner/editor/viewer
       ↓
┌──────────────────────────────┐
│         Projects             │
│ - short_name                 │
│ - taxonomy                   │
│ - sensor_layout             │
│ - observed (boolean)        │
└──────┬───────────────────────┘
       │
       ├─→ (many-to-many) ┌─────────────┐
       │                  │   Species   │
       │                  │  (targets)  │
       │                  └─────────────┘
       │
       ↓ (one-to-many)
┌──────────────────────────────┐
│        Deployments           │
│ - code                       │
│ - location (GeoPoint)        │
│ - dates                      │
│ - sensor_height              │
│ - aru (device info)          │
│ - aru_status                 │
│ - aru_mount                  │
│ - feature_type (habitat)     │
│ - frequency                  │
│ - trigger_type               │
└──────┬───────────────────────┘
       │
       ↓ (one-to-many)
┌──────────────────────────────┐
│         Recordings           │
│ - path (audio file)          │
│ - sample_rate                │
│ - start_datetime             │
│ - end_datetime               │
└──────┬───────────────────────┘
       │
       ↓ (one-to-many)
┌──────────────────────────────┐
│       Observations           │
│ - species_id                 │
│ - confidence (0-1)           │
│ - start (timestamp)          │
│ - end (timestamp)            │
│ - rank                       │
└──────────────────────────────┘
       │
       ↓ (references)
┌──────────────────────────────┐
│          Species             │
│ (lookup table)               │
└──────────────────────────────┘


┌─────────────────────────────┐
│   Reference/Lookup Tables   │
├─────────────────────────────┤
│ • Taxa (taxonomy systems)   │
│ • SensorLayouts             │
│ • FeatureTypes (habitats)   │
│ • Frequencies               │
│ • ARUMount                  │
│ • ARUStatus                 │
│ • TriggerType               │
└─────────────────────────────┘
```

---

## Workflow Examples

### Example 1: Creating a Complete Project

```
1. Create Project
   POST /projects/
   → Returns project_id

2. Add Target Species
   POST /projects/{project_id}/species/{species_id}

3. Add Project Members
   POST /projects/{project_id}/users/
   (with email and role)

4. Create Deployments
   POST /deployments/
   (reference project_id, include location)
   → Returns deployment_id

5. Upload Recordings
   POST /recordings/
   (reference project_id and deployment_id)
   → Returns recording_id

6. Add Species Observations
   POST /projects/{project_id}/observations/
   (reference recording_id and species_id)
```

### Example 2: Querying Data

```
1. Find all projects for a user
   GET /user-projects/?role=owner

2. Get all recordings in a project
   GET /projects/{project_id}/recordings/

3. Get species detections for a recording
   GET /recordings/{recording_id}/observations/

4. Get audio segment
   GET /recordings/{recording_id}/ogg?start=2024-01-01T10:00:00&end=2024-01-01T10:00:30
```

---

## Key Features

### ✅ **Spatial Analysis**
- GeoJSON Point geometries on deployments
- Aggregate projects with geometry: `GET /projects/agg/`

### ✅ **Audio Processing**
- On-demand transcoding to OGG format
- Temporal subsetting of audio files
- Sample rate preservation

### ✅ **Access Control**
- Role-based permissions (owner/editor/viewer)
- Project-level access management
- User invitation system

### ✅ **Data Organization**
- Multi-level hierarchy (Project → Deployment → Recording → Observation)
- Standardized reference data (lookup tables)
- Change tracking via Revisions

### ✅ **Filtering & Querying**
- Filter projects by taxonomy, user, observation status
- Temporal filtering on recordings
- Species-specific queries

---

## Authentication

**Method**: JWE (JSON Web Encryption) cookies
**OAuth2**: Supported with redirect endpoint at `/docs/oauth2-redirect`
**Session Management**: Cookie-based authentication for API requests

---

## Summary

The Wildlife Sound Hub API is designed for comprehensive wildlife acoustic monitoring research. It provides:

1. **Hierarchical data organization** from projects down to individual species detections
2. **Spatial capabilities** for geographic analysis of deployments
3. **Audio processing** for on-demand transcoding and temporal subsetting
4. **Access control** with role-based permissions
5. **Standardized reference data** through lookup tables
6. **Temporal analysis** with datetime filtering across all resources

The API supports the full research lifecycle from project creation through deployment, recording, and species observation analysis.
