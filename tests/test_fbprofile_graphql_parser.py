from src.fbprofile.graphql.extractors import collect_post_summaries, coalesce_posts
from src.fbprofile.graphql.parser import parse_fb_graphql_payload


def test_parse_streamed_graphql_chunks_collects_profile_posts():
    payload_text = (
        '{"data":{"node":{"timeline_list_feed_units":{"edges":[{"node":{'
        '"__typename":"Story",'
        '"__isFeedUnit":"Story",'
        '"id":"UzpfAAA",'
        '"post_id":"1417607630383403",'
        '"url":"https://www.facebook.com/photo.php?fbid=1417607343716765",'
        '"actors":[{"id":"100064025371723","name":"Kieu Vy",'
        '"url":"https://www.facebook.com/nek.tui.3557"}],'
        '"comet_sections":{"content":{"story":{'
        '"wwwURL":"https://www.facebook.com/nek.tui.3557/posts/'
        'pfbid0t8NXa4r9R3Pstd7p2izfdQTgUbdf13G1eDdGrwvJjJuHgAjt1jpRP2i7MQeCfr6xl",'
        '"message":{"text":"em nuoc tay trang"}}}},'
        '"message":{"text":"em nuoc tay trang"},'
        '"creation_time":1776691939'
        '}}]}}}} '
        '{"label":"ProfileCometTimelineFeed_user$stream","data":{"node":{'
        '"__typename":"Story",'
        '"__isFeedUnit":"Story",'
        '"id":"UzpfBBB",'
        '"post_id":"1417607000000000",'
        '"wwwURL":"https://www.facebook.com/nek.tui.3557/posts/'
        'pfbid02WvzXsQu1KnuzUhZ17xhtWoYn3fGUC3wXn1fYm45cDqNrkKf5BT7K7pUvHB6AfeRpl",'
        '"actors":[{"id":"100064025371723","name":"Kieu Vy",'
        '"url":"https://www.facebook.com/nek.tui.3557"}],'
        '"message":{"text":"second post"},'
        '"creation_time":1776690000'
        '}}}'
    )

    payload = parse_fb_graphql_payload(payload_text)

    assert isinstance(payload, list)
    assert len(payload) == 2

    posts = []
    collect_post_summaries(payload, posts, "https://www.facebook.com/nek.tui.3557")

    links = {post.get("link") for post in posts}
    rids = {post.get("rid") for post in posts}

    assert "1417607630383403" in rids
    assert "1417607000000000" in rids
    assert any(
        "pfbid0t8NXa4r9R3Pstd7p2izfdQTgUbdf13G1eDdGrwvJjJuHgAjt1jpRP2i7MQeCfr6xl" in (link or "")
        for link in links
    )
    assert any(
        "pfbid02WvzXsQu1KnuzUhZ17xhtWoYn3fGUC3wXn1fYm45cDqNrkKf5BT7K7pUvHB6AfeRpl" in (link or "")
        for link in links
    )


def test_collects_story_when_permalink_is_nested_without_direct_ids():
    payload = {
        "data": {
            "node": {
                "__typename": "Story",
                "__isFeedUnit": "Story",
                "comet_sections": {
                    "content": {
                        "story": {
                            "wwwURL": "https://www.facebook.com/nek.tui.3557/posts/pfbidSeaMakeupFirstPost",
                            "message": {
                                "text": (
                                    "Em xit khoa nen nha Sea Makeup nay dang sale sap san luon na\n"
                                    "#169k co voucher giam con #139k nha\n"
                                    "https://s.shopee.vn/7KtBy3FsiK"
                                )
                            },
                            "creation_time": 1776700000,
                        }
                    }
                },
            }
        }
    }

    posts = []
    collect_post_summaries(payload, posts, "https://www.facebook.com/nek.tui.3557")

    assert posts
    assert posts[0]["link"] == "https://www.facebook.com/nek.tui.3557/posts/pfbidSeaMakeupFirstPost"
    assert "Sea Makeup" in posts[0]["content"]
    assert posts[0]["link_share"] == "https://s.shopee.vn/7KtBy3FsiK"


def test_collects_media_viewer_container_story_as_main_post():
    payload = {
        "data": {
            "currMedia": {
                "__typename": "Photo",
                "__isMedia": "Photo",
                "id": "1417645373712962",
                "created_time": 1776695375,
                "image": {
                    "uri": "https://scontent.example/sea-makeup.jpg",
                    "width": 1024,
                    "height": 1024,
                },
                "container_story": {
                    "id": "UzpfSTEwMDA2NDAyNTM3MTcyMzoxNDE3NjQ2NzIwMzc5NDk0OjE0MTc2NDY3MjAzNzk0OTQ=",
                    "post_id": "1417646720379494",
                    "url": (
                        "https://www.facebook.com/nek.tui.3557/posts/"
                        "pfbid02WvzXsQu1KnuzUhZ17xhtWoYn3fGUC3wXn1fYm45cDqNrkKf5BT7K7pUvHB6AfeRpl"
                    ),
                    "message": {
                        "text": (
                            "Em xit khoa nen nha Sea Makeup nay dang sale sap san luon na\n"
                            "#169k co voucher giam con #139k nha\n\n"
                            "https://s.shopee.vn/7KtBy3FsiK\n\n"
                            "Nhanh tay keo het, so luong co han"
                        )
                    },
                    "actors": [
                        {
                            "id": "100064025371723",
                            "name": "Kieu Vy",
                            "url": "https://www.facebook.com/nek.tui.3557",
                        }
                    ],
                },
                "creation_story": {
                    "id": "UzpfSTEwMDA2NDAyNTM3MTcyMzpWSzoxNDE3NjQ1MzczNzEyOTYy",
                    "post_id": "1417645373712962",
                    "url": "https://www.facebook.com/photo.php?fbid=1417645373712962",
                    "message": None,
                },
            }
        }
    }

    posts = []
    collect_post_summaries(payload, posts, "https://www.facebook.com/nek.tui.3557")
    merged = coalesce_posts(posts)
    post = next((p for p in merged if p.get("rid") == "1417646720379494"), None)

    assert post is not None
    assert "pfbid02WvzXsQu1KnuzUhZ17xhtWoYn3fGUC3wXn1fYm45cDqNrkKf5BT7K7pUvHB6AfeRpl" in post["link"]
    assert "Sea Makeup" in post["content"]
    assert post["link_share"] == "https://s.shopee.vn/7KtBy3FsiK"
    assert post["created_time"] == 1776695375
    assert post["image_url"] == ["https://scontent.example/sea-makeup.jpg"]
