from crawler import (
    _resolve_anonymous_fallback_login_method,
    should_run_anonymous_first_for_target,
)


def test_first_time_page_uses_anonymous(tmp_path):
    target = {
        "uid": "https://www.facebook.com/example.page",
        "selector_module": "page",
    }

    assert should_run_anonymous_first_for_target(
        target,
        "page",
        data_root=tmp_path,
    )


def test_existing_page_posts_disable_auto_anonymous(tmp_path):
    target = {
        "uid": "https://www.facebook.com/example.page",
        "selector_module": "page",
    }
    page_dir = tmp_path / "page" / "example.page"
    page_dir.mkdir(parents=True)
    (page_dir / "posts_all.ndjson").write_text('{"rid":"1"}\n', encoding="utf-8")

    assert not should_run_anonymous_first_for_target(
        target,
        "page",
        data_root=tmp_path,
    )


def test_profile_never_uses_auto_anonymous(tmp_path):
    target = {
        "uid": "https://www.facebook.com/profile.php?id=10001",
        "selector_module": "profile",
    }

    assert not should_run_anonymous_first_for_target(
        target,
        "profile",
        data_root=tmp_path,
    )


def test_anonymous_fallback_prefers_available_session(monkeypatch):
    monkeypatch.delenv("ANONYMOUS_FIRST_FALLBACK_LOGIN_METHOD", raising=False)
    monkeypatch.delenv("AUTO_ANONYMOUS_FALLBACK_LOGIN_METHOD", raising=False)

    assert _resolve_anonymous_fallback_login_method("cookies", "") == "cookies"
    assert _resolve_anonymous_fallback_login_method("anonymous", "c_user=1") == "cookies"
    assert _resolve_anonymous_fallback_login_method("anonymous", "") == "profile"

    monkeypatch.setenv("ANONYMOUS_FIRST_FALLBACK_LOGIN_METHOD", "none")
    assert _resolve_anonymous_fallback_login_method("anonymous", "c_user=1") is None
