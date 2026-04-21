import json
from pathlib import Path

from groups.group_media_pipeline import (
    build_group_info_document,
    build_media_post_urls,
    extract_media_ids,
)


BASE_DIR = Path(__file__).resolve().parents[1]


def _load_json(path: str):
    return json.loads((BASE_DIR / path).read_text(encoding="utf-8"))


def test_extract_media_ids_deduplicates_and_keeps_order():
    payload = _load_json("groups/group_info_input.json")
    media_ids = extract_media_ids(payload)

    assert media_ids
    assert media_ids[0] == "122210088464329177"
    assert len(media_ids) == len(set(media_ids))


def test_build_group_info_document_maps_expected_fields():
    payload = _load_json("groups/group_info_input.json")

    document = build_group_info_document(
        group_url="https://www.facebook.com/groups/804362789744484",
        first_page=payload,
        scanned_at="2026-04-11 13:06:11",
        photo_limit=3,
    )

    assert document["url"] == "https://www.facebook.com/groups/804362789744484"
    assert document["scanned_at"] == "2026-04-11 13:06:11"
    assert document["basic_info"]["group_id"] == "804362789744484"
    assert document["basic_info"]["reference_token"] == "g.804362789744484"
    assert document["basic_info"]["entity_type"] == "facebook_group"
    assert document["viewer_capabilities"]["can_post"] is None
    assert document["viewer_capabilities"]["can_create_album"] is None
    assert len(document["photos"]) == 3


def test_build_media_post_urls_returns_both_supported_patterns():
    urls = build_media_post_urls("123456")

    assert urls == [
        "https://www.facebook.com/photo?fbid=123456&set=pcb.123456",
        "https://www.facebook.com/photo?fbid=123456&set=a.123456",
    ]
