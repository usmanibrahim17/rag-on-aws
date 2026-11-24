#!/usr/bin/env bash
# Build Lambda deployment zips into dist/. Run from anywhere.
set -euo pipefail

cd "$(dirname "$0")/.."
PY="$(command -v python3 || command -v python)"

# Vendor the pure-Python dependencies (pypdf) next to our code. --only-binary refuses source
# builds, so only a ready-made pure-Python wheel can be installed: it runs on Lambda's Linux
# whatever machine builds it.
rm -rf dist/_vendor
"$PY" -m pip install --quiet --no-compile --only-binary=:all: \
  --target dist/_vendor -r requirements-lambda.txt

# Both Lambdas ship the same package (shared code in app/common); the handler setting in
# Terraform picks which entry point runs. Separate zips keep deploys independent.
"$PY" scripts/_build_zip.py dist/ingest.zip dist/_vendor
"$PY" scripts/_build_zip.py dist/query.zip dist/_vendor
