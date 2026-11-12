#!/usr/bin/env bash
# Build Lambda deployment zips into dist/. Run from anywhere.
set -euo pipefail

cd "$(dirname "$0")/.."
PY="$(command -v python3 || command -v python)"

# Both Lambdas ship the same app/ package (shared code in app/common); the handler setting
# in Terraform picks which entry point runs. Separate zips keep deploys independent.
"$PY" scripts/_build_zip.py dist/ingest.zip
"$PY" scripts/_build_zip.py dist/query.zip
