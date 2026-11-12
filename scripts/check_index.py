"""List the vectors in the S3 Vectors index and summarize them (no embedding values).

    python scripts/check_index.py --bucket <vector bucket> --index <index> [--profile NAME]

Prints the total count, a count per source_key, and one sample vector's metadata.
Needs s3vectors:ListVectors, which the Lambda deliberately does not have; this runs
under your own CLI credentials.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Iterator

import boto3

PAGE_SIZE = 500  # list_vectors accepts up to 1000 per page
MISSING = "(missing source_key)"


def iter_vectors(client, bucket: str, index: str) -> Iterator[dict]:
    """Yield every vector (key + metadata, no data), following nextToken pagination."""
    token = None
    while True:
        kwargs = {
            "vectorBucketName": bucket,
            "indexName": index,
            "maxResults": PAGE_SIZE,
            "returnMetadata": True,
            "returnData": False,
        }
        if token:
            kwargs["nextToken"] = token
        page = client.list_vectors(**kwargs)
        yield from page.get("vectors", [])
        token = page.get("nextToken")
        if not token:
            return


def summarize(vectors) -> dict:
    """Return {"total", "per_source": {source_key: count}, "sample": first vector or None}."""
    per_source: Counter = Counter()
    total = 0
    sample = None
    for vector in vectors:
        total += 1
        if sample is None:
            sample = vector
        per_source[(vector.get("metadata") or {}).get("source_key", MISSING)] += 1
    return {"total": total, "per_source": dict(sorted(per_source.items())), "sample": sample}


def format_report(summary: dict) -> str:
    lines = [f"Total vectors: {summary['total']}", "", "Per source_key:"]
    if summary["per_source"]:
        width = max(len(k) for k in summary["per_source"])
        lines += [f"  {k.ljust(width)}  {n}" for k, n in summary["per_source"].items()]
    else:
        lines.append("  (none)")

    sample = summary["sample"]
    if sample:
        meta = dict(sample.get("metadata") or {})
        text = meta.get("chunk_text")
        if isinstance(text, str) and len(text) > 200:
            meta["chunk_text"] = text[:200] + "..."  # keep the output readable
        lines += ["", f"Sample vector key: {sample.get('key')}", "Sample metadata:"]
        lines += [f"  {k}: {v!r}" for k, v in meta.items()]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bucket", required=True, help="vector bucket name")
    parser.add_argument("--index", required=True, help="vector index name")
    parser.add_argument("--profile", help="AWS CLI profile (optional)")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args(argv)

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    client = session.client("s3vectors")
    print(format_report(summarize(iter_vectors(client, args.bucket, args.index))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
