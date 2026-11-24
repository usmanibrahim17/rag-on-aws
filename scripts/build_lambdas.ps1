# Build Lambda deployment zips into dist/. Run from anywhere (PowerShell).
$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

# Vendor the pure-Python dependencies (pypdf) next to our code. --only-binary refuses source
# builds, so only a ready-made pure-Python wheel can be installed: it runs on Lambda's Linux
# whatever machine builds it.
if (Test-Path dist/_vendor) { Remove-Item -Recurse -Force dist/_vendor }
python -m pip install --quiet --no-compile --only-binary=:all: --target dist/_vendor -r requirements-lambda.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Both Lambdas ship the same package (shared code in app/common); the handler setting in
# Terraform picks which entry point runs. Separate zips keep deploys independent.
foreach ($name in "ingest", "query") {
    python scripts/_build_zip.py "dist/$name.zip" dist/_vendor
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
