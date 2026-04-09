"""Remote selector config download, caching, and lookup helpers."""

from __future__ import annotations

import json  # parse selector payloads and metadata
import os  # file paths for cache
import re  # sanitize cache keys
import logging
from datetime import datetime  # timestamps for cache metadata
from typing import Any, Dict, Iterable, Tuple  # type hints

import requests  # HTTP requests to selector service

from src.utils.env import build_service_url, str_to_bool  # env flag parsing

logger = logging.getLogger(__name__)

DEFAULT_SELECTOR_ENDPOINT = (
    "https://latex-card-walk-donor.trycloudflare.com/configs/auto-node"
)
DEFAULT_SELECTOR_LOGIN_URL = (
    "https://latex-card-walk-donor.trycloudflare.com/public/login"
)
DEFAULT_SELECTOR_CACHE_DIR = "./selector_cache"
DEFAULT_REQUEST_TIMEOUT = 15
DEFAULT_SELECTOR_USERNAME = "admin"
DEFAULT_SELECTOR_PASSWORD = "admin123"


def resolve_selector_payload(
    local_selector: Dict[str, Any] | None,
    env: Dict[str, str],
    *,
    endpoint: str | None = None,
    cache_dir: str | None = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> Tuple[Dict[str, Any] | None, str]:
    """
    Resolve selector payload with this priority:
    1) Remote (downloaded)
    2) Cache (if remote fails)
    3) Local config (config.json)

    Returns (payload, source) where source is: remote|cache|local|none.
    """
    # Allow explicit disable via env to keep current behavior when needed.
    auto_download = str_to_bool(env.get("SELECTOR_AUTO_DOWNLOAD"), True)
    if not auto_download:
        return local_selector, "local" if local_selector else "none"

    # Figure out target selectors based on env override or local metadata.
    site = env.get("SELECTOR_SITE") or (local_selector or {}).get("site")
    environment = env.get("SELECTOR_ENV") or (local_selector or {}).get("environment")
    module = env.get("SELECTOR_MODULE") or (local_selector or {}).get("module")
    page = env.get("SELECTOR_PAGE") or (local_selector or {}).get("page")

    # If we don't know site/env, we can't request a specific config.
    if not site or not environment:
        return local_selector, "local" if local_selector else "none"

    selected_endpoint = (
        endpoint
        or build_service_url(
            env,
            path="/configs/auto-node",
            explicit_key="SELECTOR_ENDPOINT",
            fallback=DEFAULT_SELECTOR_ENDPOINT,
        )
    ).strip()
    selected_cache_dir = (cache_dir or env.get("SELECTOR_CACHE_DIR") or DEFAULT_SELECTOR_CACHE_DIR).strip()

    payload, source = download_selector_with_cache(
        selected_endpoint,
        site=site,
        environment=environment,
        module=module,
        page=page,
        cache_dir=selected_cache_dir,
        timeout=timeout,
        env=env,
    )

    if payload is not None:
        return payload, source

    return local_selector, "local" if local_selector else "none"


def download_selector_with_cache(
    endpoint: str,
    *,
    site: str,
    environment: str,
    module: str | None,
    page: str | None,
    cache_dir: str,
    timeout: int,
    env: Dict[str, str] | None = None,
) -> Tuple[Dict[str, Any] | None, str]:
    """
    Download selector JSON and cache it locally.
    Falls back to cache when download fails.
    """
    cache_path, meta_path = build_cache_paths(cache_dir, site, environment, module, page)
    cached_payload = read_json_file(cache_path)
    cached_meta = read_json_file(meta_path) or {}

    # Login before download. If login fails, we must fall back to cache and stop.
    auth_headers = login_before_download(env or {}, timeout=timeout)
    if auth_headers is None:
        return cached_payload, "cache" if cached_payload else "none"

    headers = {"accept": "application/json"}
    headers.update(auth_headers)
    # Use ETag/Last-Modified if available to reduce API calls.
    if isinstance(cached_meta.get("etag"), str):
        headers["If-None-Match"] = cached_meta["etag"]
    if isinstance(cached_meta.get("last_modified"), str):
        headers["If-Modified-Since"] = cached_meta["last_modified"]

    params: Dict[str, str] = {"site": site, "environment": environment}
    if module:
        params["module"] = module
    if page:
        params["page"] = page

    try:
        response = requests.get(
            endpoint,
            headers=headers,
            params=params,
            timeout=timeout,
        )
    except Exception as exc:  # network/timeouts/etc.
        # Clear warning when we cannot download; fallback to cache if possible.
        logger.warning(
            "[selectors] download failed (%s). Using cached config if available.",
            exc,
        )
        return cached_payload, "cache" if cached_payload else "none"

    if response.status_code == 304:
        # Server says cache is still valid.
        return cached_payload, "cache" if cached_payload else "none"

    if response.status_code != 200:
        logger.warning(
            "[selectors] WARN: download failed with "
            f"status={response.status_code}. Using cached config if available."
        )
        return cached_payload, "cache" if cached_payload else "none"

    try:
        data = response.json()
    except ValueError:
        logger.warning(
            "[selectors] download returned invalid JSON. Using cached config if available."
        )
        return cached_payload, "cache" if cached_payload else "none"

    payload = pick_selector_payload(data, site, environment, module, page)
    if payload is None:
        logger.warning(
            "[selectors] WARN: download JSON did not contain matching selector config. "
            "Using cached config if available."
        )
        return cached_payload, "cache" if cached_payload else "none"

    # Decide whether we need to write to cache (based on version/etag/updated_at).
    if should_update_cache(payload, cached_meta, response.headers):
        write_json_file(cache_path, payload)
        meta = build_meta(payload, response.headers, site, environment, module, page, endpoint)
        write_json_file(meta_path, meta)

    return payload, "remote"


def login_before_download(env: Dict[str, str], timeout: int) -> Dict[str, str] | None:
    """
    Login to BE before downloading selector JSON.
    Returns auth headers or None if login fails.
    """
    login_url = build_service_url(
        env,
        path="/public/login",
        explicit_key="SELECTOR_LOGIN_URL",
        fallback=DEFAULT_SELECTOR_LOGIN_URL,
    ).strip()
    username = env.get("SELECTOR_USERNAME", DEFAULT_SELECTOR_USERNAME)
    password = env.get("SELECTOR_PASSWORD", DEFAULT_SELECTOR_PASSWORD)

    if not login_url:
        logger.warning("[selectors] login URL is empty; cannot authenticate.")
        return None

    payload = {"username": username, "password": password}
    try:
        response = requests.post(
            login_url,
            headers={"accept": "application/json", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning(
            "[selectors] login failed (%s). Using cached config if available.",
            exc,
        )
        return None

    if response.status_code != 200:
        logger.warning(
            "[selectors] WARN: login failed with "
            f"status={response.status_code}. Using cached config if available."
        )
        return None

    try:
        data = response.json() or {}
    except ValueError:
        logger.warning(
            "[selectors] login returned invalid JSON. Using cached config if available."
        )
        return None

    token = extract_token(data)
    if not token:
        logger.warning(
            "[selectors] login response missing token. Using cached config if available."
        )
        return None
    logger.debug("[selectors] received auth token for selector download.")
    return {"Authorization": f"Bearer {token}"}


def extract_token(data: Any) -> str | None:
    """
    Try common token fields to avoid tight coupling with BE response schema.
    """
    if not isinstance(data, dict):
        return None
    # Support both snake_case and camelCase from Swagger response.
    for key in ("access_token", "accessToken", "token", "jwt"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in ("access_token", "accessToken", "token", "jwt"):
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def build_cache_paths(
    cache_dir: str,
    site: str,
    environment: str,
    module: str | None,
    page: str | None,
) -> Tuple[str, str]:
    """Build cache and metadata paths for selector configs."""
    os.makedirs(cache_dir, exist_ok=True)
    parts = [
        f"site={slugify(site)}",
        f"env={slugify(environment)}",
    ]
    if module:
        parts.append(f"module={slugify(module)}")
    if page:
        parts.append(f"page={slugify(page)}")
    filename = "__".join(parts) + ".json"
    cache_path = os.path.join(cache_dir, filename)
    meta_path = cache_path + ".meta.json"
    return cache_path, meta_path


def pick_selector_payload(
    data: Any,
    site: str,
    environment: str,
    module: str | None,
    page: str | None,
) -> Dict[str, Any] | None:
    """Pick the best matching selector payload from API data."""
    # Normalize response payload to a list of candidates.
    candidates: Iterable[Any] = []
    if isinstance(data, dict):
        if "elements" in data:
            candidates = [data]
        elif "selectors" in data and isinstance(data.get("selectors"), dict):
            selectors_block = data.get("selectors")
            if isinstance(selectors_block, dict) and "elements" in selectors_block:
                candidates = [selectors_block]
        elif "data" in data:
            nested = data.get("data")
            if isinstance(nested, dict) and "elements" in nested:
                candidates = [nested]
            else:
                candidates = nested
        elif "items" in data:
            nested = data.get("items")
            if isinstance(nested, dict) and "elements" in nested:
                candidates = [nested]
            else:
                candidates = nested
        else:
            candidates = []
    else:
        candidates = data

    if not isinstance(candidates, list):
        return None

    matched: list[Dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        # Only enforce filters if BE provides the field. If metadata is missing,
        # we allow the item to pass and rely on updated_at/version selection.
        if site and item.get("site") is not None and item.get("site") != site:
            continue
        if (
            environment
            and item.get("environment") is not None
            and item.get("environment") != environment
        ):
            continue
        if module and item.get("module") is not None and item.get("module") != module:
            continue
        if page and item.get("page") is not None and item.get("page") != page:
            continue
        matched.append(item)

    if not matched:
        return None
    if len(matched) == 1:
        return matched[0]

    # If multiple configs match, prefer the most recently updated.
    matched.sort(key=lambda item: parse_updated_at(item.get("updated_at")) or datetime.min)
    return matched[-1]


def should_update_cache(
    payload: Dict[str, Any],
    cached_meta: Dict[str, Any],
    headers: Dict[str, Any],
) -> bool:
    """Return True if cache should be refreshed based on metadata."""
    if not cached_meta:
        return True

    new_etag = headers.get("ETag") or headers.get("etag")
    if new_etag and cached_meta.get("etag") != new_etag:
        return True

    # Compare version/updated_at if ETag is absent or unchanged.
    if payload.get("version") != cached_meta.get("version"):
        return True
    if payload.get("updated_at") != cached_meta.get("updated_at"):
        return True

    return False


def build_meta(
    payload: Dict[str, Any],
    headers: Dict[str, Any],
    site: str,
    environment: str,
    module: str | None,
    page: str | None,
    endpoint: str,
) -> Dict[str, Any]:
    """Build cache metadata for selector payloads."""
    return {
        "site": site,
        "environment": environment,
        "module": module,
        "page": page,
        "version": payload.get("version"),
        "updated_at": payload.get("updated_at"),
        "etag": headers.get("ETag") or headers.get("etag"),
        "last_modified": headers.get("Last-Modified") or headers.get("last-modified"),
        "endpoint": endpoint,
        "cached_at": datetime.utcnow().isoformat() + "Z",
    }


def parse_updated_at(value: Any) -> datetime | None:
    """Parse updated_at values into datetime, if possible."""
    if not isinstance(value, str) or not value:
        return None
    try:
        # Support timestamps with trailing Z (UTC).
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def slugify(value: str) -> str:
    """Normalize strings for use in cache filenames."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned or "unknown"


def read_json_file(path: str) -> Dict[str, Any] | None:
    """Read a JSON file and return a dict when possible."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_json_file(path: str, payload: Dict[str, Any]) -> None:
    """Write a dict payload to a JSON file."""
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
