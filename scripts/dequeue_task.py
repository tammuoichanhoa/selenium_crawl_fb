#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from src.utils import build_service_url, load_env_file

DEFAULT_DEQUEUE_PATH = "/tasks/dequeue?social_type=facebook&version=1.0"


@dataclass(frozen=True)
class DequeueResult:
    ok: bool
    status_code: int
    payload: dict[str, Any] | None = None
    text: str = ""
    error: str = ""
    url: str = ""

    def json(self) -> dict[str, Any]:
        return self.payload or {}


def resolve_dequeue_url() -> str:
    env = load_env_file(".env")
    url = build_service_url(
        env,
        path=DEFAULT_DEQUEUE_PATH,
        explicit_key="DEQUEUE_URL",
        fallback="",
    ).strip()
    if not url:
        raise ValueError("Missing DEQUEUE_URL or SERVICE_ROOT_URL for dequeue request.")
    return url


def run_request(api_key: str, url: str | None = None, timeout: int = 10) -> DequeueResult:
    resolved_url = url or resolve_dequeue_url()
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        response = requests.get(resolved_url, headers=headers, timeout=timeout)
        response_text = response.text or ""
        payload: dict[str, Any] | None = None
        if response_text.strip():
            try:
                parsed = response.json()
                if not isinstance(parsed, dict):
                    return DequeueResult(
                        ok=False,
                        status_code=response.status_code,
                        text=response_text,
                        error="Dequeue response JSON must be an object.",
                        url=resolved_url,
                    )
                payload = parsed
            except ValueError as exc:
                return DequeueResult(
                    ok=False,
                    status_code=response.status_code,
                    text=response_text,
                    error=f"Invalid JSON response: {exc}",
                    url=resolved_url,
                )

        if not response.ok:
            return DequeueResult(
                ok=False,
                status_code=response.status_code,
                payload=payload,
                text=response_text,
                error=response_text.strip() or response.reason,
                url=resolved_url,
            )

        return DequeueResult(
            ok=True,
            status_code=response.status_code,
            payload=payload or {},
            text=response_text,
            url=resolved_url,
        )
    except requests.exceptions.RequestException as e:
        return DequeueResult(
            ok=False,
            status_code=0,
            error=str(e),
            url=resolved_url,
        )


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

    try:
        result = run_request(args.api_key)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    header = (
        f"timestamp_utc={timestamp}\n"
        f"ok={str(result.ok).lower()}\n"
        f"status_code={result.status_code}\n"
        f"error={result.error}\n"
        f"url={result.url}\n"
        f"command=curl -X GET <url> -H 'accept: application/json' "
        f"-H 'Authorization: Bearer ***'\n"
    )

    body = (
        json.dumps(result.json(), ensure_ascii=False, indent=2)
        if result.payload is not None
        else result.text
    )
    output = header + "\n" + body

    try:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
    except OSError as e:
        print(f"Failed to write output file: {e}", file=sys.stderr)
        return 3

    if body:
        print(body)
    if result.error:
        print(result.error, file=sys.stderr)

    return 0 if result.ok else 1

if __name__ == "__main__":
    sys.exit(main())
