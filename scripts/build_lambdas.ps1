# Build Lambda deployment zips into dist/. Run from anywhere (PowerShell).
$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

# Both Lambdas ship the same app/ package (shared code in app/common); the handler setting
# in Terraform picks which entry point runs. Separate zips keep deploys independent.
foreach ($name in "ingest", "query") {
    python scripts/_build_zip.py "dist/$name.zip"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
