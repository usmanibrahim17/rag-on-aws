#!/usr/bin/env bash
# Build Lambda deployment zips into dist/. Run from anywhere.
set -euo pipefail

cd "$(dirname "$0")/.."
PY="$(command -v python3 || command -v python)"

# One zip for now; the query Lambda reuses the same app/ package in M3.
"$PY" scripts/_build_zip.py dist/ingest.zip
