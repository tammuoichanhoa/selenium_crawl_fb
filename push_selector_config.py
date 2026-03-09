from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict

import requests

from utils.env import load_env_file
from utils.selector_remote import login_before_download


DEFAULT_PUT_ENDPOINT = (
    "https://gasoline-asn-protecting-pictures.trycloudflare.com/configs/auto-node"
)


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("JSON must be an object at top-level.")
    return data


def infer_payload(
    data: Dict[str, Any],
    *,
    site: str | None,
    environment: str | None,
    version: str | None,
    updated_by: str | None,
) -> Dict[str, Any]:
    """
    Build payload for PUT /configs/auto-node.
    Accept either:
    - full config (with 'selectors' block)
    - selectors-only JSON (with 'elements' and metadata)
    """
    if "selectors" in data and isinstance(data.get("selectors"), dict):
        payload = data
        selectors = payload["selectors"]
    else:
        # If file looks like selectors-only, wrap it under 'selectors'.
        if "elements" in data or "site" in data or "environment" in data:
            payload = {"selectors": data}
            selectors = payload["selectors"]
        else:
            raise ValueError(
                "JSON must contain 'selectors' or look like selectors-only (with 'elements')."
            )

    # Apply metadata overrides (CLI/env has priority).
    if site:
        selectors["site"] = site
    if environment:
        selectors["environment"] = environment
    if version:
        selectors["version"] = version
    if updated_by:
        selectors["updated_by"] = updated_by

    # Validate required metadata before pushing.
    missing = [
        key
        for key in ("site", "environment", "version", "updated_by")
        if not selectors.get(key)
    ]
    if missing:
        raise ValueError(f"Missing required selector metadata: {', '.join(missing)}")

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Push selector JSON to BE.")
    parser.add_argument("--file", default="config.json", help="Path to JSON file.")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("SELECTOR_PUT_ENDPOINT", DEFAULT_PUT_ENDPOINT),
        help="PUT endpoint for /configs/auto-node.",
    )
    parser.add_argument("--site", default=os.environ.get("SELECTOR_SITE"))
    parser.add_argument("--environment", default=os.environ.get("SELECTOR_ENV"))
    parser.add_argument("--version", default=os.environ.get("SELECTOR_VERSION"))
    parser.add_argument("--updated-by", dest="updated_by", default=os.environ.get("SELECTOR_UPDATED_BY"))

    args = parser.parse_args()

    # Load env for login credentials and login URL.
    env = load_env_file(".env")
    auth_headers = login_before_download(env, timeout=15)
    if auth_headers is None:
        raise SystemExit("Login failed. Abort push.")

    data = load_json(args.file)
    payload = infer_payload(
        data,
        site=args.site,
        environment=args.environment,
        version=args.version,
        updated_by=args.updated_by,
    )

    headers = {"accept": "application/json", "Content-Type": "application/json"}
    headers.update(auth_headers)

    response = requests.put(
        args.endpoint,
        headers=headers,
        json=payload,
        timeout=20,
    )

    # Print minimal response info for verification.
    print("status:", response.status_code)
    try:
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    except ValueError:
        print(response.text)


if __name__ == "__main__":
    main()
