from __future__ import annotations

from .config import DEFAULT_CONFIG_PATH, load_config
from .cookies import parse_cookie_string
from .drivers import (
    create_local_driver,
    create_logged_in_driver,
    get_facebook_login_debug_state,
    login_facebook_with_cookies,
    terminate_chrome_process,
    verify_facebook_login_state,
)
from .env import load_env_file, str_to_bool
from .pages import read_pages, resolve_max_workers, split_pages_for_workers
from .ports import build_port_queue, is_port_free
from .profile_backup import backup_profile_folder
from .profiles import parse_profile_dirs, resolve_profile_dirs
from .proxies import DEFAULT_PROXIES_FILE, select_working_proxy
from .selectors import (
    build_locator_chain,
    extract_element,
    guard_fragile_locators,
    normalize_elements_config,
    resolve_by,
    resolve_locator,
    resolve_wait_state,
    resolve_wait_timeout_seconds,
    validate_selector_payload,
)
from .selector_remote import resolve_selector_payload
from .waits import (
    wait_for_body,
    wait_for_document_ready,
    wait_for_page_ready,
    wait_for_seconds,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_PROXIES_FILE",
    "backup_profile_folder",
    "build_locator_chain",
    "build_port_queue",
    "create_local_driver",
    "create_logged_in_driver",
    "extract_element",
    "guard_fragile_locators",
    "get_facebook_login_debug_state",
    "is_port_free",
    "load_config",
    "load_env_file",
    "login_facebook_with_cookies",
    "normalize_elements_config",
    "parse_cookie_string",
    "parse_profile_dirs",
    "read_pages",
    "resolve_selector_payload",
    "resolve_by",
    "resolve_locator",
    "resolve_max_workers",
    "resolve_profile_dirs",
    "resolve_wait_state",
    "resolve_wait_timeout_seconds",
    "select_working_proxy",
    "split_pages_for_workers",
    "str_to_bool",
    "terminate_chrome_process",
    "validate_selector_payload",
    "verify_facebook_login_state",
    "wait_for_body",
    "wait_for_document_ready",
    "wait_for_page_ready",
    "wait_for_seconds",
]
