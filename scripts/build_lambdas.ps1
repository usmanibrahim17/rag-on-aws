# Build Lambda deployment zips into dist/. Run from anywhere (PowerShell).
$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

# One zip for now; the query Lambda reuses the same app/ package in M3.
python scripts/_build_zip.py dist/ingest.zip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
