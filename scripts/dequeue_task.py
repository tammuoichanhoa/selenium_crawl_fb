#!/usr/bin/env python3
import argparse
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone

def run_curl(api_key: str) -> subprocess.CompletedProcess:
    url = (
        "https://gasoline-asn-protecting-pictures.trycloudflare.com/"
        "tasks/dequeue?social_type=facebook&version=1.0"
    )
    cmd = [
        "curl",
        "-sS",
        "-X",
        "GET",
        url,
        "-H",
        "accept: application/json",
        "-H",
        f"Authorization: Bearer {api_key}",
    ]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run dequeue task request and print the result."
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("API_KEY"),
        help="API key for Authorization header (or set API_KEY env var).",
    )
    parser.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(__file__), "dequeue_result.txt"),
        help="Output file to write response body and metadata.",
    )
    args = parser.parse_args()

    if not args.api_key:
        print("Missing API key. Provide --api-key or set API_KEY env var.", file=sys.stderr)
        return 2

    result = run_curl(args.api_key)

    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    header = (
        f"timestamp_utc={timestamp}\n"
        f"exit_code={result.returncode}\n"
        f"stderr={result.stderr.strip()}\n"
        f"command=curl -X POST <url> -H 'accept: application/json' "
        f"-H 'Authorization: Bearer ***'\n"
    )

    output = header + "\n" + (result.stdout or "")

    try:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
    except OSError as e:
        print(f"Failed to write output file: {e}", file=sys.stderr)
        return 3

    # Also print response body to stdout for immediate visibility
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    return result.returncode

if __name__ == "__main__":
    sys.exit(main())
