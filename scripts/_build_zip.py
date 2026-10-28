"""Zip the app/ package for Lambda: python scripts/_build_zip.py dist/ingest.zip

Used by build_lambdas.sh and build_lambdas.ps1 so both produce identical zips. Writing the
archive with Python guarantees forward-slash entry names (Windows PowerShell 5.1's
Compress-Archive writes backslashes, which break on Linux). boto3 is already in the Lambda
Python runtime, so only our own code goes in.
"""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXED_TIME = (2020, 1, 1, 0, 0, 0)  # constant timestamp -> same input gives byte-identical zip


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out = Path(sys.argv[1])
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in (ROOT / "app").rglob("*.py") if "__pycache__" not in p.parts)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16  # rw-r--r--, readable by the Lambda user
            zf.writestr(info, path.read_bytes())

    print(f"wrote {out} ({len(files)} files, {out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
