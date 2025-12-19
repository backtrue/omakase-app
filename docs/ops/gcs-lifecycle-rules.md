# GCS Uploads Retention Policy

## Overview

This document describes the lifecycle rules for the GCS bucket used for temporary scan image uploads.

## Bucket Configuration

- **Bucket Name**: `omakase-scans-prod`
- **Region**: `asia-east1`
- **Purpose**: Temporary storage for user-uploaded menu images during scan processing

## Lifecycle Rules

### Rule 1: Delete uploads after 24 hours

User-uploaded images in the `uploads/` prefix are temporary processing artifacts and should be deleted after 24 hours.

```json
{
  "lifecycle": {
    "rule": [
      {
        "action": {
          "type": "Delete"
        },
        "condition": {
          "age": 1,
          "matchesPrefix": ["uploads/"]
        }
      }
    ]
  }
}
```

### Rationale

1. **Privacy**: User images should not be retained longer than necessary for processing
2. **Cost**: Reduces storage costs by removing temporary files
3. **Compliance**: Aligns with privacy policy stating images are not stored permanently

## Applying the Lifecycle Rule

### Using gcloud CLI

```bash
# Create lifecycle config file
cat > /tmp/lifecycle.json << 'EOF'
{
  "lifecycle": {
    "rule": [
      {
        "action": {
          "type": "Delete"
        },
        "condition": {
          "age": 1,
          "matchesPrefix": ["uploads/"]
        }
      }
    ]
  }
}
EOF

# Apply to bucket
gcloud storage buckets update gs://omakase-scans-prod --lifecycle-file=/tmp/lifecycle.json
```

### Using Google Cloud Console

1. Go to Cloud Storage > Buckets
2. Select `omakase-scans-prod`
3. Go to "Lifecycle" tab
4. Click "Add a rule"
5. Configure:
   - Action: Delete object
   - Condition: Age = 1 day
   - Object name matches prefix: `uploads/`
6. Save

## Verification

### Check current lifecycle rules

```bash
gcloud storage buckets describe gs://omakase-scans-prod --format="json(lifecycle)"
```

### Verify objects are being deleted

```bash
# List objects older than 1 day in uploads/
gcloud storage ls -l gs://omakase-scans-prod/uploads/ | head -20

# Check bucket size over time in Cloud Monitoring
```

## Related Documents

- Privacy Policy: `docs/privacy-policy/index.md`
- Architecture: `spec/00_ARCHITECTURE.md`
- Image upload implementation: `backend/app/jobs.py`

## Changelog

- 2025-12-19: Initial documentation for V1.1a release
