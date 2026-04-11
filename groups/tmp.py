#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests


GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
DEFAULT_DOC_ID = "5341536295888250"
DEFAULT_USER_ID = "100064454655705"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/96.0.4664.110 Safari/537.36"
)


def explain_inputs() -> str:
    return """Input parameters:
- --doc-id: Facebook GraphQL document ID. This identifies the internal query template that the endpoint will execute.
- --user-id: Facebook user/page/profile ID injected into the GraphQL variables payload as userID.
- --width: Requested response render width. Often used for image-related GraphQL fields.
- --height: Requested response render height. Often paired with width for image sizing.
- --scale: Output scale multiplier, usually 1 or 2 depending on device pixel ratio.
- --user-agent: HTTP User-Agent header sent with the request.
- --cookie: Raw Cookie header string. Use this when you already have a logged-in Facebook session.
- --cookie-file: File containing the raw Cookie string. Default is cookie_string.txt.
- --timeout: Request timeout in seconds.

Behavior:
- Cookie priority: --cookie overrides --cookie-file.
- variables payload sent to Facebook: {"height": ..., "scale": ..., "userID": ..., "width": ...}
- endpoint: https://www.facebook.com/api/graphql/

Example:
python3 tmp.py --doc-id 5341536295888250 --user-id 100064454655705 --cookie-file cookie_string.txt
"""


def load_cookie(raw_cookie: str | None, cookie_file: str | None) -> str:
    if raw_cookie:
        return raw_cookie.strip()
    if cookie_file:
        return Path(cookie_file).read_text(encoding="utf-8").strip()
    return ""


def build_headers(user_agent: str, cookie: str) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.facebook.com",
        "Referer": "https://www.facebook.com/",
        "Sec-Fetch-Site": "same-origin",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def main() -> int:
    parser = argparse.ArgumentParser(description="Call a Facebook GraphQL endpoint.")
    parser.add_argument("--doc-id", default=DEFAULT_DOC_ID)
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--width", type=int, default=500)
    parser.add_argument("--height", type=int, default=500)
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--cookie", help="Raw Cookie header value.")
    parser.add_argument(
        "--cookie-file",
        default="cookie_string.txt",
        help="Path to a file containing the raw cookie string.",
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--explain-inputs",
        action="store_true",
        help="Print an explanation of the input parameters and exit.",
    )
    args = parser.parse_args()

    if args.explain_inputs:
        print(explain_inputs())
        return 0

    cookie = load_cookie(args.cookie, args.cookie_file)
    variables = {
        "height": args.height,
        "scale": args.scale,
        "userID": args.user_id,
        "width": args.width,
    }

    response = requests.post(
        GRAPHQL_URL,
        params={
            "doc_id": args.doc_id,
            "variables": json.dumps(variables, separators=(",", ":")),
        },
        headers=build_headers(args.user_agent, cookie),
        data={},
        timeout=args.timeout,
    )
    response.raise_for_status()

    try:
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    except ValueError:
        print(response.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
