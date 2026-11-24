"""Zip the app/ package (plus vendored packages) for Lambda:

    python scripts/_build_zip.py dist/ingest.zip [vendor-dir]

`vendor-dir` is a folder of pip-installed pure-Python packages (see requirements-lambda.txt)
that is added at the zip root, next to app/, so `import pypdf` works inside Lambda.

Used by build_lambdas.sh and build_lambdas.ps1 so both produce identical zips. Writing the
archive with Python guarantees forward-slash entry names (Windows PowerShell 5.1's
Compress-Archive writes backslashes, which break on Linux). boto3 is already in the Lambda
Python runtime, so only our own code and the vendored packages go in.
"""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXED_TIME = (2020, 1, 1, 0, 0, 0)  # constant timestamp -> same input gives byte-identical zip


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        return 2
    out = Path(sys.argv[1])
    vendor = Path(sys.argv[2]) if len(sys.argv) == 3 else None
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)

    # (path on disk, name inside the zip). Sorted, so the same input gives the same zip.
    entries = [
        (p, p.relative_to(ROOT).as_posix())
        for p in (ROOT / "app").rglob("*.py")
        if "__pycache__" not in p.parts
    ]
    if vendor is not None:
        if not vendor.is_dir():
            print(f"vendor directory not found: {vendor}", file=sys.stderr)
            return 1
        entries += [
            (p, p.relative_to(vendor).as_posix())
            for p in vendor.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and not any(part.endswith(".dist-info") for part in p.parts)
            and p.relative_to(vendor).parts[0] != "bin"
        ]
    entries.sort(key=lambda e: e[1])

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, name in entries:
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16  # rw-r--r--, readable by the Lambda user
            zf.writestr(info, path.read_bytes())

    print(f"wrote {out} ({len(entries)} files, {out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
