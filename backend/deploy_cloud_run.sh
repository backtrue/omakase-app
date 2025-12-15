#!/usr/bin/env bash
set -euo pipefail

SERVICE="${CLOUD_RUN_SERVICE:-omakase-api}"
REGION="${CLOUD_RUN_REGION:-asia-east1}"
PROJECT="${CLOUD_RUN_PROJECT:-}"
SOURCE_DIR="${CLOUD_RUN_SOURCE_DIR:-.}"

args=("run" "deploy" "${SERVICE}" "--region" "${REGION}" "--source" "${SOURCE_DIR}")

if [[ -n "${PROJECT}" ]]; then
  args+=("--project" "${PROJECT}")
fi

exec gcloud "${args[@]}"
