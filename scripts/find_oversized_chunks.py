#!/usr/bin/env python3
"""Extract file paths that were indexed with oversized chunks (token count > 512).

Queries GCP Cloud Logging for tokenizer warnings and correlates them with
the subsequent "Processed" log from the same pod to extract the file_path.

Usage:
    python3 scripts/find_oversized_chunks.py [--limit N] [--output FILE]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta

PROJECT = "ca-it-prod-cicd-428e"
NAMESPACE = "vista-rpms-archive"


def gcloud_read(filter_str: str, limit: int = 1000) -> list[dict]:
    """Run gcloud logging read and return parsed JSON entries."""
    cmd = [
        "gcloud",
        "logging",
        "read",
        filter_str,
        f"--project={PROJECT}",
        f"--limit={limit}",
        "--format=json",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        return []
    return json.loads(result.stdout) if result.stdout.strip() else []


def extract_token_count(text: str) -> int:
    """Parse token count from warning message like '(1234 > 512)'."""
    try:
        return int(text.split("(")[1].split(" >")[0])
    except (IndexError, ValueError):
        return 0


def find_processed_log(pod_name: str, after_ts: str) -> str | None:
    """Find the next 'Processed' log entry from the same pod after the warning."""
    # Parse timestamp and add a 120s window
    dt = datetime.fromisoformat(after_ts.replace("Z", "+00:00"))
    end_dt = dt + timedelta(seconds=120)

    filter_str = (
        f'resource.type="k8s_container" '
        f'resource.labels.namespace_name="{NAMESPACE}" '
        f'resource.labels.pod_name="{pod_name}" '
        f'jsonPayload.message=~"^Processed " '
        f'timestamp>="{dt.isoformat()}" '
        f'timestamp<="{end_dt.isoformat()}"'
    )
    entries = gcloud_read(filter_str, limit=1)
    if entries:
        return entries[0].get("jsonPayload", {}).get("file_path", "")
    return None


def main():
    parser = argparse.ArgumentParser(description="Find files indexed with oversized chunks")
    parser.add_argument("--limit", type=int, default=10000, help="Max warning entries to fetch")
    parser.add_argument("--output", type=str, default="oversized_chunks.json", help="Output file")
    parser.add_argument(
        "--warnings-only",
        action="store_true",
        help="Just extract warnings (skip per-pod correlation)",
    )
    args = parser.parse_args()

    print(f"Fetching up to {args.limit} tokenizer warnings from GCP logs...")

    # Step 1: Get all tokenizer warnings
    filter_str = (
        f'resource.type="k8s_container" '
        f'resource.labels.namespace_name="{NAMESPACE}" '
        f'"Token indices sequence length"'
    )
    warnings = gcloud_read(filter_str, limit=args.limit)
    print(f"Found {len(warnings)} tokenizer warnings")

    # Group by pod+timestamp
    results = []
    seen_pods_ts = set()

    for entry in warnings:
        pod = entry["resource"]["labels"]["pod_name"]
        ts = entry["timestamp"]
        text = entry.get("textPayload", "")
        tokens = extract_token_count(text)

        key = f"{pod}:{ts}"
        if key in seen_pods_ts:
            continue
        seen_pods_ts.add(key)

        record = {
            "pod": pod,
            "timestamp": ts,
            "token_count": tokens,
            "file_path": None,
        }

        if not args.warnings_only:
            # Step 2: Correlate with Processed log
            file_path = find_processed_log(pod, ts)
            record["file_path"] = file_path
            status = f"-> {file_path}" if file_path else "-> (not found)"
            print(f"  [{len(results) + 1}] {tokens} tokens | {pod} | {status}")

        results.append(record)

    # Deduplicate by file_path
    unique_paths = set()
    for r in results:
        if r["file_path"]:
            unique_paths.add(r["file_path"])

    summary = {
        "total_warnings": len(results),
        "unique_files": len(unique_paths),
        "files": sorted(unique_paths),
        "details": results,
    }

    with open(args.output, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"Total warnings: {len(results)}")
    print(f"Unique files:   {len(unique_paths)}")
    print(f"Saved to:       {args.output}")

    # Also print just the paths for easy piping
    paths_file = args.output.replace(".json", "_paths.txt")
    with open(paths_file, "w") as f:
        for p in sorted(unique_paths):
            f.write(p + "\n")
    print(f"Paths list:     {paths_file}")


if __name__ == "__main__":
    main()
