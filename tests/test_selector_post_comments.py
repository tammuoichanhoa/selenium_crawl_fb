from src.fbprofile.browser import selector_posts


def test_extract_comments_collects_direct_comment_texts_and_filters_noise(monkeypatch):
    def fake_collect_texts(root, selectors, selector_name=None):
        if selector_name == "group.posts.comments":
            return [
                "Bình luận đầu tiên",
                "  Bình luận đầu tiên  ",
                "Thích",
                "123",
                "Trả lời",
                "Bình luận thứ hai",
                "Phù hợp nhất",
            ]
        return []

    monkeypatch.setattr(selector_posts, "_collect_texts", fake_collect_texts)
    monkeypatch.setattr(selector_posts, "_scroll_post_for_comments", lambda driver, post_element, source_url: None)

    result = selector_posts._extract_comments(object(), object(), "https://www.facebook.com/groups/demo")

    assert result == ["Bình luận đầu tiên", "Bình luận thứ hai"]


def test_extract_comments_returns_empty_when_no_direct_comment_texts(monkeypatch):
    def fake_collect_texts(root, selectors, selector_name=None):
        return ["Comment A", "Reply", "Comment B", "Comment A"] if selector_name == "group.posts.comments" else []

    monkeypatch.setattr(selector_posts, "_collect_texts", fake_collect_texts)
    monkeypatch.setattr(selector_posts, "_scroll_post_for_comments", lambda driver, post_element, source_url: None)

    result = selector_posts._extract_comments(object(), object(), "https://www.facebook.com/groups/demo")

    assert result == ["Comment A", "Comment B"]


def test_extract_comments_scrolls_before_collecting(monkeypatch):
    calls = []

    monkeypatch.setattr(selector_posts, "_safe_find_elements", lambda root, selectors, selector_name=None: [])
    monkeypatch.setattr(selector_posts, "_collect_texts", lambda root, selectors, selector_name=None: [])
    monkeypatch.setattr(
        selector_posts,
        "_scroll_post_for_comments",
        lambda driver, post_element, source_url: calls.append((driver, post_element, source_url)),
    )

    driver = object()
    post_element = object()
    source_url = "https://www.facebook.com/groups/demo"
    selector_posts._extract_comments(driver, post_element, source_url)

    assert calls == [(driver, post_element, source_url)]
