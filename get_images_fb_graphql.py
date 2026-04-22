from __future__ import annotations
import os
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import requests

GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
DEFAULT_USER_AGENT = "Mozilla/5.0"
# Dùng chung 1 DOC_ID cho cả trang đầu và các trang sau (chỉ khác nhau ở biến cursor)
DOC_ID_GROUP_MEDIA = "26372875982381439"

# =============================================================
#                            CLASSES 
# =============================================================
class FacebookGraphQLResponseError(RuntimeError):
    """Raised when Facebook returns a non-JSON or error payload."""

@dataclass(frozen=True)
class GraphQLTokens:
    fb_dtsg: str | None = None
    lsd: str | None = None
    jazoest: str | None = None
    av: str | None = None

    @property
    def is_complete(self) -> bool:
        return bool(self.fb_dtsg and self.lsd and self.jazoest and self.av)


# =============================================================
#                            LIBS 
# =============================================================
def _cookie_value(cookie_header: str, name: str) -> str | None:
    pattern = re.compile(rf"(?:^|;\s*){re.escape(name)}=([^;]+)")
    match = pattern.search(cookie_header or "")
    if not match:
        return None
    value = (match.group(1) or "").strip()
    return value or None

def compute_jazoest(fb_dtsg: str | None) -> str | None:
    token = (fb_dtsg or "").strip()
    if not token:
        return None
    return "2" + "".join(str(ord(char)) for char in token)

def _extract_token(html: str, token_name: str) -> str | None:
    patterns = [
        rf'\["{re.escape(token_name)}",\[\],\{{"token":"(.*?)"\}}\]',
        rf'"{re.escape(token_name)}",\[\],\{{"token":"(.*?)"\}}',
        rf'"{re.escape(token_name)}"\s*:\s*\{{"token":"(.*?)"\}}',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            value = (match.group(1) or "").strip()
            if value:
                return value
    return None

def extract_graphql_tokens_from_html(
    html: str,
    cookie_header: str,
    *,
    fb_dtsg: str | None = None,
) -> GraphQLTokens:
    resolved_dtsg = (fb_dtsg or "").strip() or _extract_token(html, "DTSGInitialData")
    lsd = _extract_token(html, "LSD")
    av = _cookie_value(cookie_header, "c_user")
    return GraphQLTokens(
        fb_dtsg=resolved_dtsg or None,
        lsd=lsd,
        jazoest=compute_jazoest(resolved_dtsg),
        av=av,
    )

def _normalize_tokens(
    cookie_header: str,
    *,
    tokens: GraphQLTokens | None = None,
    fb_dtsg: str | None = None,
) -> GraphQLTokens:
    base = tokens or GraphQLTokens()
    resolved_dtsg = (fb_dtsg or "").strip() or base.fb_dtsg
    return GraphQLTokens(
        fb_dtsg=resolved_dtsg,
        lsd=base.lsd,
        jazoest=base.jazoest or compute_jazoest(resolved_dtsg),
        av=base.av or _cookie_value(cookie_header, "c_user"),
    )

def _extract_media_root(response: dict[str, Any]) -> dict[str, Any] | None:
    data = response.get("data") or {}
    root = data.get("group")
    if isinstance(root, dict):
        return root

    root = data.get("node")
    if isinstance(root, dict):
        return root

    return None

def extract_media_page(response: dict[str, Any]) -> dict[str, Any]:
    root = _extract_media_root(response) or {}
    mediaset = root.get("group_mediaset") or {}
    media = mediaset.get("media") or {}

    edges = media.get("edges")
    if not isinstance(edges, list):
        edges = []

    photos: list[dict[str, Any]] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue

        node = edge.get("node")
        if not isinstance(node, dict):
            continue

        image = node.get("image") or {}
        viewer_image_orig = node.get("viewer_image_orig") or {}
        owner = node.get("owner") or {}
        photo_id = node.get("id")
        uri = image.get("uri")

        if not isinstance(photo_id, str) or not photo_id.strip():
            continue
        if not isinstance(uri, str) or not uri.strip():
            continue

        photos.append(
            {
                "id": photo_id,
                "uri": uri,
                "caption": node.get("accessibility_caption"),
                "width": viewer_image_orig.get("width"),
                "height": viewer_image_orig.get("height"),
                "feedback_id": (node.get("feedback") or {}).get("id"),
                "is_playable": node.get("is_playable"),
                "owner": {
                    "id": owner.get("id"),
                    "type": owner.get("__typename"),
                },
                "cursor": edge.get("cursor"),
            }
        )

    page_info = media.get("page_info") or {}
    return {
        "group_id": root.get("id"),
        "group_name": root.get("name"),
        "reference_token": mediaset.get("reference_token"),
        "can_post": root.get("if_viewer_can_post"),
        "can_create_album": root.get("if_viewer_can_create_album"),
        "photos": photos,
        "cursor": page_info.get("end_cursor"),
        "has_next": bool(page_info.get("has_next_page")),
    }


# =============================================================
#                    BUILD GRAPHQL REQUESTS 
# =============================================================
def _build_headers(user_agent: str, cookie_header: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie_header,
        "Accept": "*/*",
        "Origin": "https://www.facebook.com",
        "Referer": "https://www.facebook.com/",
        "X-FB-Friendly-Name": "GroupsCometPhotosTabContentRefetchQuery",
    }

def _base_graphql_payload(tokens: GraphQLTokens) -> dict[str, str]:
    payload: dict[str, str] = {
        "server_timestamps": "true",
        "api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": "GroupsCometPhotosTabContentRefetchQuery",
    }

    if tokens.av:
        payload["av"] = tokens.av
        payload["__user"] = tokens.av

    if tokens.fb_dtsg:
        payload["fb_dtsg"] = tokens.fb_dtsg

    if tokens.lsd:
        payload["lsd"] = tokens.lsd

    if tokens.jazoest:
        payload["jazoest"] = tokens.jazoest

    return payload

def build_media_payload(
    group_id: str,
    cursor: str | None,
    *,
    tokens: GraphQLTokens,
    scale: int = 1,
) -> dict[str, str]:
    payload = _base_graphql_payload(tokens)
    
    variables = {
        "scale": scale,
        "useCometPhotoViewerPlaceholderFrag": False,
        "id": group_id,
    }
    
    # Nếu vòng lặp truyền cursor vào, đẩy vào JSON, nếu None (trang đầu tiên) thì bỏ qua
    if cursor:
        variables["cursor"] = cursor
        
    payload.update(
        {
            "doc_id": DOC_ID_GROUP_MEDIA,
            "variables": json.dumps(variables, separators=(",", ":")),
        }
    )
    return payload

def _raise_graphql_error(response: requests.Response) -> None:
    content_type = (response.headers.get("content-type") or "").strip()
    body_preview = (response.text or "").strip()[:500]
    raise FacebookGraphQLResponseError(
        "Facebook GraphQL returned a non-JSON response. "
        f"status={response.status_code} content_type={content_type!r} "
        f"body_preview={body_preview!r}"
    )

def _post_graphql(
    payload: dict[str, Any],
    *,
    cookie_header: str,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    response = requests.post(
        GRAPHQL_URL,
        headers=_build_headers(user_agent, cookie_header),
        data=payload,
        timeout=30,
    )
    response.raise_for_status()

    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise FacebookGraphQLResponseError(
            f"Facebook GraphQL response is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        _raise_graphql_error(response)

    if data.get("error"):
        raise FacebookGraphQLResponseError(
            f"Facebook GraphQL returned an error payload: {data['error']!r}"
        )
        
    os.makedirs('outputs', exist_ok=True)
    with open('outputs/response.json', "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    return data

def fetch_media_response(
    group_id: str,
    cursor: str | None,
    *,
    cookie_header: str,
    user_agent: str = DEFAULT_USER_AGENT,
    fb_dtsg: str | None = None,
    tokens: GraphQLTokens | None = None,
    scale: int = 1,
) -> dict[str, Any]:
    resolved_tokens = _normalize_tokens(
        cookie_header,
        tokens=tokens,
        fb_dtsg=fb_dtsg,
    )
    payload = build_media_payload(
        group_id,
        cursor,
        tokens=resolved_tokens,
        scale=scale,
    )
    return _post_graphql(
        payload,
        cookie_header=cookie_header,
        user_agent=user_agent,
    )


# =============================================================
#                    CRAWLERS
# =============================================================
def crawl_group(
    group_id: str,
    user_agent: str,
    cookie: str,
    fb_dtsg: str | None = None,
    tokens: GraphQLTokens | None = None,
    max_pages: int = 3,
) -> dict[str, Any]:
    
    result = {
        "url": f"https://www.facebook.com/groups/{group_id}",
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "basic_info": {
            "group_id": group_id,
            "reference_token": f"g.{group_id}",
            "entity_type": "facebook_group",
            "name": None,
            "members": None,
            "avatar_url": None,
            "cover_photo": None,
        },
        "viewer_capabilities": {
            "can_post": None,
            "can_create_album": None,
        },
        "photos": [],
        "members": [],
        "posts": [],
    }

    seen_photo_ids: set[str] = set()

    def merge_media_page(page_data: dict[str, Any]) -> None:
        if page_data.get("reference_token"):
            result["basic_info"]["reference_token"] = page_data["reference_token"]
        if page_data.get("group_name"):
            result["basic_info"]["name"] = page_data["group_name"]
        if page_data.get("group_id"):
            result["basic_info"]["group_id"] = page_data["group_id"]
        if page_data.get("can_post") is not None:
            result["viewer_capabilities"]["can_post"] = page_data["can_post"]
        if page_data.get("can_create_album") is not None:
            result["viewer_capabilities"]["can_create_album"] = page_data["can_create_album"]

        for photo in page_data.get("photos", []):
            photo_id = photo.get("id")
            if not isinstance(photo_id, str) or photo_id in seen_photo_ids:
                continue
            seen_photo_ids.add(photo_id)
            result["photos"].append(photo)

    cursor = None
    has_next = True
    page_count = 0
    unlimited_pages = max_pages == -1

    # Dùng chung 1 vòng lặp cho trang đầu (cursor=None) và các trang sau
    while has_next and (unlimited_pages or page_count < max_pages):
        print(f"Đang fetch trang media {page_count + 1} (Cursor: {cursor})")

        group_info = fetch_media_response(
            group_id,
            cursor=cursor,
            cookie_header=cookie,
            user_agent=user_agent,
            fb_dtsg=fb_dtsg,
            tokens=tokens,
            scale=1,
        )

        media_page = extract_media_page(group_info)
        merge_media_page(media_page)

        # Cập nhật cursor cho vòng lặp kế tiếp
        cursor = media_page["cursor"]
        has_next = media_page["has_next"]
        page_count += 1
        
        # Delay tránh block
        time.sleep(2)

    return result

# =============================================================
#                    MAIN PROGRAM
# =============================================================
if __name__ == "__main__":
    USER_AGENT = DEFAULT_USER_AGENT
    COOKIE = "locale=vi_VN; c_user=XXX; xs=XXX;"
    FB_DTSG = None
    group_id = "677277369688449"

    # cao du lieu group
    data = crawl_group(group_id, USER_AGENT, COOKIE, FB_DTSG, max_pages=100000000000000000000000)

    # xuat ket qua ra file json
    output_path = Path(__file__).parent / "outputs" / "group_info.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )