#!/usr/bin/env python3
import argparse
import requests
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from src.utils import load_env_file
DEFAULT_DEQUEUE_URL = load_env_file(".env").get("DEQUEUE_URL", "https://latex-card-walk-donor.trycloudflare.com/tasks/dequeue?social_type=facebook&version=1.0")


def _load_env_value(key: str, default: str = "") -> str:
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".env",
    )
    if not os.path.exists(env_path):
        return default

    with open(env_path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            current_key, value = line.split("=", 1)
            if current_key.strip() == key:
                return value.strip().strip('"').strip("'")
    return default


def _build_service_url(
    *,
    path: str,
    root_key: str = "SERVICE_ROOT_URL",
    explicit_key: str | None = None,
    fallback: str = "",
) -> str:
    if explicit_key:
        explicit_value = _load_env_value(explicit_key, "").strip()
        if explicit_value:
            return explicit_value

    root_value = _load_env_value(root_key, "").strip().rstrip("/")
    if root_value:
        return f"{root_value}/{path.lstrip('/')}"

    return fallback


def run_request(api_key: str):
    # nếu bạn muốn override như code cũ
    url = DEFAULT_DEQUEUE_URL
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)

        # raise exception nếu status != 200
        response.raise_for_status()
        return response  # hoặc response.json()
    
    except requests.exceptions.RequestException as e:
        print(f"Request error: {e}")
        return None

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

    result = run_request(args.api_key)

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
