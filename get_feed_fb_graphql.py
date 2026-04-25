from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

DOC_ID = "26372875982381439"
FRIENDLY_NAME = "GroupsCometFeedRegularStoriesPaginationQuery"


class FacebookGraphQLResponseError(RuntimeError):
    pass


def parse_streaming_json_objects(raw_text: str) -> list[dict[str, Any]]:
    """
    Facebook response kiểu stream:
      {json1}
      {json2}
      {json3}
    requests.Response.json() sẽ lỗi Extra data.
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return []

    decoder = json.JSONDecoder()
    idx = 0
    out: list[dict[str, Any]] = []

    while idx < len(raw_text):
        while idx < len(raw_text) and raw_text[idx].isspace():
            idx += 1
        if idx >= len(raw_text):
            break

        obj, next_idx = decoder.raw_decode(raw_text, idx)
        if isinstance(obj, dict):
            out.append(obj)
        idx = next_idx

    return out


def build_headers(
    *,
    user_agent: str,
    cookie_header: str,
    referer_group_id: str,
    x_fb_lsd: str | None = None,
) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.facebook.com",
        "Referer": f"https://www.facebook.com/groups/{referer_group_id}/",
        "Cookie": cookie_header,
        "X-FB-Friendly-Name": FRIENDLY_NAME,
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    if x_fb_lsd:
        headers["X-FB-LSD"] = x_fb_lsd
    return headers


def build_variables(group_id: str, cursor: str | None, count: int, scale: int) -> dict[str, Any]:
    variables = {
        "count": count,
        "cursor": cursor,
        "feedLocation": "GROUP",
        "feedType": "DISCUSSION",
        "feedbackSource": 0,
        "filterTopicId": None,
        "focusCommentID": None,
        "privacySelectorRenderLocation": "COMET_STREAM",
        "referringStoryRenderLocation": None,
        "renderLocation": "group",
        "scale": scale,
        "sortingSetting": "CHRONOLOGICAL",
        "stream_initial_count": 1,
        "useDefaultActor": False,
        "id": group_id,
        "__relay_internal__pv__GHLShouldChangeAdIdFieldNamerelayprovider": False,
        "__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider": False,
        "__relay_internal__pv__CometFeedStory_enable_post_permalink_white_space_clickrelayprovider": False,
        "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
        "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
        "__relay_internal__pv__IsWorkUserrelayprovider": False,
        "__relay_internal__pv__TestPilotShouldIncludeDemoAdUseCaserelayprovider": False,
        "__relay_internal__pv__FBReels_deprecate_short_form_video_context_gkrelayprovider": False,
        "__relay_internal__pv__FBReels_enable_view_dubbed_audio_type_gkrelayprovider": False,
        "__relay_internal__pv__CometImmersivePhotoCanUserDisable3DMotionrelayprovider": False,
        "__relay_internal__pv__WorkCometIsEmployeeGKProviderrelayprovider": False,
        "__relay_internal__pv__IsMergQAPollsrelayprovider": False,
        "__relay_internal__pv__FBReelsMediaFooter_comet_enable_reels_ads_gkrelayprovider": True,
        "__relay_internal__pv__CometUFIReactionsEnableShortNamerelayprovider": False,
        "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "ORIGINAL",
        "__relay_internal__pv__CometUFIShareActionMigrationrelayprovider": True,
        "__relay_internal__pv__CometUFISingleLineUFIrelayprovider": False,
        "__relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider": True,
        "__relay_internal__pv__FBReelsIFUTileContent_reelsIFUPlayOnHoverrelayprovider": False,
        "__relay_internal__pv__GroupsCometGYSJFeedItemHeightrelayprovider": 150,
        "__relay_internal__pv__ShouldEnableBakedInTextStoriesrelayprovider": False,
        "__relay_internal__pv__StoriesShouldIncludeFbNotesrelayprovider": False,
    }
    return variables


def build_payload(
    *,
    group_id: str,
    cursor: str | None,
    lsd: str,
    count: int = 3,
    scale: int = 1,
) -> dict[str, str]:
    variables = build_variables(group_id, cursor, count, scale)

    payload = {
        # theo logged-out payload bạn chụp
        "av": "0",
        "__aaid": "0",
        "__user": "0",
        "__a": "1",
        "__req": "1",
        "__hs": "20565.HYP:comet_loggedout_pkg.2.1.0",
        "dpr": "1",
        "__ccg": "GOOD",
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": FRIENDLY_NAME,
        "server_timestamps": "true",
        "lsd": lsd,
        "variables": json.dumps(variables, separators=(",", ":"), ensure_ascii=False),
        "doc_id": DOC_ID,
        "fb_api_analytics_tags": json.dumps(["qpl_active_flow_ids=431626709"]),
    }
    return payload


def post_graphql(
    *,
    group_id: str,
    cookie_header: str,
    lsd: str,
    cursor: str | None,
    count: int = 3,
    scale: int = 1,
    user_agent: str = DEFAULT_USER_AGENT,
    debug: bool = False,
) -> list[dict[str, Any]]:
    payload = build_payload(
        group_id=group_id,
        cursor=cursor,
        lsd=lsd,
        count=count,
        scale=scale,
    )

    response = requests.post(
        GRAPHQL_URL,
        headers=build_headers(
            user_agent=user_agent,
            cookie_header=cookie_header,
            referer_group_id=group_id,
            x_fb_lsd=lsd,
        ),
        data=payload,
        timeout=30,
    )
    response.raise_for_status()

    raw_text = response.text or ""

    if debug:
        print("STATUS:", response.status_code)
        print("CONTENT-TYPE:", response.headers.get("content-type"))
        print("RAW RESPONSE PREVIEW:")
        print(raw_text[:3000])

    try:
        objects = parse_streaming_json_objects(raw_text)
    except json.JSONDecodeError as exc:
        raise FacebookGraphQLResponseError(
            f"Không parse được response stream: {exc}\nPreview: {raw_text[:1000]}"
        ) from exc

    if not objects:
        raise FacebookGraphQLResponseError(
            f"Response rỗng hoặc không có JSON hợp lệ.\nPreview: {raw_text[:1000]}"
        )

    return objects


def extract_errors(stream_objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for obj in stream_objects:
        obj_errors = obj.get("errors")
        if isinstance(obj_errors, list):
            for item in obj_errors:
                if isinstance(item, dict):
                    errors.append(item)
    return errors


def extract_group_id(stream_objects: list[dict[str, Any]]) -> str | None:
    for obj in stream_objects:
        data = obj.get("data")
        if not isinstance(data, dict):
            continue
        node = data.get("node")
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            return node["id"]
    return None


def extract_stream_cursors(stream_objects: list[dict[str, Any]]) -> list[str]:
    cursors: list[str] = []

    for obj in stream_objects:
        cursor = obj.get("cursor")
        if isinstance(cursor, str):
            cursors.append(cursor)

    return cursors


def extract_simple_items(stream_objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Query này là feed stream, object stream thường nằm ở level:
      {"label": ..., "path": ..., "data": {"node": ...}, "cursor": "..."}
    Mình rút ra item đơn giản để bạn debug/crawl tiếp.
    """
    items: list[dict[str, Any]] = []

    for obj in stream_objects:
        data = obj.get("data")
        if not isinstance(data, dict):
            continue

        node = data.get("node")
        if not isinstance(node, dict):
            continue

        item = {
            "id": node.get("id"),
            "typename": node.get("__typename"),
            "cursor": obj.get("cursor"),
            "title_text": None,
            "raw": node,
        }

        title = node.get("title")
        if isinstance(title, dict) and isinstance(title.get("text"), str):
            item["title_text"] = title["text"]

        items.append(item)

    return items


def fetch_feed_page(
    *,
    group_id: str,
    cookie_header: str,
    lsd: str,
    cursor: str | None,
    page_size: int = 3,
    user_agent: str = DEFAULT_USER_AGENT,
    debug: bool = False,
) -> dict[str, Any]:
    stream_objects = post_graphql(
        group_id=group_id,
        cookie_header=cookie_header,
        lsd=lsd,
        cursor=cursor,
        count=page_size,
        scale=1,
        user_agent=user_agent,
        debug=debug,
    )

    return {
        "group_id": extract_group_id(stream_objects) or group_id,
        "errors": extract_errors(stream_objects),
        "items": extract_simple_items(stream_objects),
        "stream_cursors": extract_stream_cursors(stream_objects),
        "raw_stream_objects": stream_objects,
    }


def crawl_group_feed_logged_out(
    *,
    group_id: str,
    cookie_header: str,
    lsd: str,
    max_pages: int = 3,
    page_size: int = 3,
    sleep_seconds: float = 2.0,
    user_agent: str = DEFAULT_USER_AGENT,
    debug: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "url": f"https://www.facebook.com/groups/{group_id}",
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "group_id": group_id,
        "pages_fetched": 0,
        "items": [],
        "errors": [],
        "last_cursor": None,
    }

    seen_keys: set[str] = set()
    cursor: str | None = None

    for page_no in range(1, max_pages + 1):
        print(f"Đang fetch trang feed {page_no} (cursor={cursor})")

        page = fetch_feed_page(
            group_id=group_id,
            cookie_header=cookie_header,
            lsd=lsd,
            cursor=cursor,
            page_size=page_size,
            user_agent=user_agent,
            debug=debug,
        )

        for err in page["errors"]:
            result["errors"].append(err)

        added = 0
        for item in page["items"]:
            key = f"{item.get('id')}|{item.get('cursor')}|{item.get('typename')}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            result["items"].append(item)
            added += 1

        next_cursor = None
        if page["stream_cursors"]:
            next_cursor = page["stream_cursors"][-1]

        result["pages_fetched"] = page_no
        result["last_cursor"] = next_cursor

        print(f"  + thêm {added} item")
        print(f"  + errors: {len(page['errors'])}")
        print(f"  + next_cursor: {next_cursor}")

        if not next_cursor or next_cursor == cursor:
            print("  -> không còn cursor mới, dừng.")
            break

        cursor = next_cursor
        time.sleep(sleep_seconds)

    return result


if __name__ == "__main__":
    GROUP_ID = "677277369688449"

    # Cookie có thể để ít field trước, nếu fail thì paste nguyên cookie từ browser
    COOKIE = "locale=vi_VN; datr=...; sb=...;"

    # lấy từ request/payload hoặc header x-fb-lsd
    LSD = "AdSa0gVxA7DZZ2sy1FBrgbJ1mu0"

    data = crawl_group_feed_logged_out(
        group_id=GROUP_ID,
        cookie_header=COOKIE,
        lsd=LSD,
        max_pages=5,
        page_size=3,
        sleep_seconds=2.0,
        debug=True,
    )

    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    output_path = output_dir / "group_feed_logged_out.json"
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\nDone. Total items: {len(data['items'])}")
    print(f"Saved to: {output_path}")