"""Structured logging configuration helpers."""

from __future__ import annotations

import getpass
import json
import logging
import os
import queue
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import requests


DEFAULT_LOG_DIR = Path("logs") / "logs"
DEFAULT_LOG_FILE = DEFAULT_LOG_DIR / "crawl.jsonl"
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 10
DEFAULT_REMOTE_LOG_BATCH_SIZE = 25
DEFAULT_REMOTE_LOG_FLUSH_INTERVAL = 2.0
DEFAULT_REMOTE_LOG_TIMEOUT = 5.0
DEFAULT_REMOTE_LOG_QUEUE_SIZE = 1000

_RESERVED_RECORD_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


def _parse_int_env(name: str, default: int) -> int:
    raw = _get_env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _parse_float_env(name: str, default: float) -> float:
    raw = _get_env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = _get_env(name)
    if raw is None:
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _load_dotenv_values(path: str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return env
    try:
        with env_path.open("r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return env


_DOTENV_VALUES: dict[str, str] | None = None


def _get_env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value not in (None, ""):
        return value

    global _DOTENV_VALUES
    if _DOTENV_VALUES is None:
        _DOTENV_VALUES = _load_dotenv_values()
    value = _DOTENV_VALUES.get(name) if _DOTENV_VALUES else None
    if value not in (None, ""):
        return value
    return default


def _resolve_log_file() -> Path:
    raw = _get_env("LOG_FILE")
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_LOG_FILE


def _resolve_run_id() -> str:
    value = _get_env("RUN_ID") or _get_env("CRAWL_RUN_ID")
    if value:
        return value.strip()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{run_id}-{uuid.uuid4().hex[:8]}"


def _resolve_run_by() -> str:
    return (
        _get_env("LOG_RUN_BY")
        or _get_env("RUN_BY")
        or _get_env("USERNAME")
        or _get_env("USER")
        or getpass.getuser()
        or "unknown"
    )


def _resolve_remote_log_url() -> str:
    explicit_url = (
        _get_env("NODE_LOGS_URL")
        or _get_env("LOG_REMOTE_URL")
        or _get_env("REMOTE_LOG_URL")
        or ""
    ).strip()
    if explicit_url:
        return explicit_url

    events_url = (_get_env("EVENTS_URL") or "").strip()
    if not events_url:
        return ""

    parsed = urlparse(events_url)
    if parsed.scheme and parsed.netloc:
        return urlunparse(parsed._replace(path="/node-logs", params="", query="", fragment=""))
    return events_url.rstrip("/").rsplit("/", 1)[0] + "/node-logs"


def _resolve_remote_log_token() -> str:
    return (
        _get_env("NODE_LOGS_TOKEN")
        or _get_env("NODE_LOGS_API_KEY")
        or _get_env("LOG_REMOTE_TOKEN")
        or _get_env("API_KEY")
        or ""
    ).strip()


def _level_from_env(env_name: str, default: str) -> int:
    level_name = (_get_env(env_name) or default).upper()
    return getattr(logging, level_name, getattr(logging, default.upper(), logging.WARNING))


def _configure_noisy_dependency_loggers() -> None:
    default_level = (_get_env("THIRD_PARTY_LOG_LEVEL") or "WARNING").upper()
    logger_levels = {
        "selenium": _level_from_env("SELENIUM_LOG_LEVEL", default_level),
        "selenium.webdriver": _level_from_env("SELENIUM_LOG_LEVEL", default_level),
        "selenium.webdriver.remote": _level_from_env("SELENIUM_LOG_LEVEL", default_level),
        "selenium.webdriver.remote.remote_connection": _level_from_env(
            "SELENIUM_LOG_LEVEL",
            default_level,
        ),
        "urllib3": _level_from_env("URLLIB3_LOG_LEVEL", default_level),
        "urllib3.connectionpool": _level_from_env("URLLIB3_LOG_LEVEL", default_level),
    }
    for logger_name, logger_level in logger_levels.items():
        logging.getLogger(logger_name).setLevel(logger_level)


class RuntimeContextFilter(logging.Filter):
    def __init__(self, run_id: str, run_by: str, node_id: str, host: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.run_by = run_by
        self.node_id = node_id
        self.host = host

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        record.run_by = self.run_by
        record.node_id = self.node_id
        record.host = self.host
        return True


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record_time_utc = datetime.fromtimestamp(record.created, timezone.utc)
        record_time_local = datetime.fromtimestamp(record.created).astimezone()
        payload: dict[str, Any] = {
            "ts": record_time_utc.isoformat(timespec="milliseconds"),
            "ts_local": record_time_local.isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "run_by": getattr(record, "run_by", None),
            "node_id": getattr(record, "node_id", None),
            "host": getattr(record, "host", None),
            "pid": record.process,
            "process": record.processName,
            "thread": record.threadName,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "file": record.pathname,
            "cwd": os.getcwd(),
        }

        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_RECORD_ATTRS
            and key not in {"run_id", "run_by", "node_id", "host"}
            and not key.startswith("_")
        }
        if extra:
            payload["extra"] = extra
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotating file handler that tolerates Windows file-lock rollover races."""

    def __init__(
        self,
        *args: Any,
        rollover_retry_interval: float = 30.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.rollover_retry_interval = max(1.0, float(rollover_retry_interval))
        self._rollover_blocked_until = 0.0
        self._last_rollover_warning_at = 0.0

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self._rollover_blocked_until > time.monotonic():
            return False
        return super().shouldRollover(record)

    def doRollover(self) -> None:
        try:
            super().doRollover()
            self._rollover_blocked_until = 0.0
        except OSError as exc:
            if not isinstance(exc, PermissionError) and getattr(exc, "winerror", None) != 32:
                raise
            now = time.monotonic()
            self._rollover_blocked_until = now + self.rollover_retry_interval
            if now - self._last_rollover_warning_at >= self.rollover_retry_interval:
                self._last_rollover_warning_at = now
                sys.stderr.write(
                    "[logging] skip rollover because log file is locked: "
                    f"{self.baseFilename} ({exc})\n"
                )
            try:
                if self.stream:
                    self.stream.flush()
            except Exception:
                pass


class RemoteNDJSONFormatter(StructuredJsonFormatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = json.loads(super().format(record))
        payload["level"] = str(payload.get("level") or "").lower()
        if isinstance(payload.get("ts"), str):
            payload["ts"] = payload["ts"].replace("+00:00", "Z")
        return json.dumps(payload, ensure_ascii=False, default=str)


class RemoteNDJSONLogHandler(logging.Handler):
    """Asynchronously POST formatted log records as application/x-ndjson."""

    def __init__(
        self,
        url: str,
        *,
        token: str = "",
        batch_size: int = DEFAULT_REMOTE_LOG_BATCH_SIZE,
        flush_interval: float = DEFAULT_REMOTE_LOG_FLUSH_INTERVAL,
        timeout: float = DEFAULT_REMOTE_LOG_TIMEOUT,
        queue_size: int = DEFAULT_REMOTE_LOG_QUEUE_SIZE,
    ) -> None:
        super().__init__()
        self.url = url
        self.token = token
        self.batch_size = max(1, int(batch_size))
        self.flush_interval = max(0.1, float(flush_interval))
        self.timeout = max(0.1, float(timeout))
        self.queue: queue.Queue[str | None] = queue.Queue(maxsize=max(1, int(queue_size)))
        self._closed = False
        self._thread = threading.Thread(
            target=self._worker,
            name="remote-log-sender",
            daemon=True,
        )
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        try:
            self.queue.put_nowait(self.format(record))
        except queue.Full:
            return
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=self.timeout + self.flush_interval + 1)
        super().close()

    def _worker(self) -> None:
        batch: list[str] = []
        last_flush = time.monotonic()
        while True:
            timeout = max(0.1, self.flush_interval - (time.monotonic() - last_flush))
            try:
                item = self.queue.get(timeout=timeout)
            except queue.Empty:
                item = None
                should_stop = False
            else:
                should_stop = item is None

            if item:
                batch.append(item)

            should_flush = (
                len(batch) >= self.batch_size
                or (batch and time.monotonic() - last_flush >= self.flush_interval)
                or should_stop
            )
            if should_flush:
                self._send_batch(batch)
                batch.clear()
                last_flush = time.monotonic()

            if should_stop:
                break

    def _send_batch(self, batch: list[str]) -> None:
        if not batch:
            return
        headers = {"Content-Type": "application/x-ndjson"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = "\n".join(batch) + "\n"
        try:
            response = requests.post(
                self.url,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=self.timeout,
            )
            if not response.ok:
                sys.stderr.write(
                    f"[remote-log] HTTP {response.status_code} while sending logs to {self.url}\n"
                )
        except Exception as exc:
            sys.stderr.write(f"[remote-log] failed to send logs to {self.url}: {exc}\n")


def setup_logging(level: Optional[str] = None) -> None:
    """Configure root logging with console output and rotating JSONL file logs."""
    _configure_noisy_dependency_loggers()

    level_name = (level or _get_env("LOG_LEVEL") or "INFO").upper()
    level_value = getattr(logging, level_name, logging.INFO)
    file_level_name = (_get_env("LOG_FILE_LEVEL") or "DEBUG").upper()
    file_level_value = getattr(logging, file_level_name, logging.DEBUG)
    remote_url = _resolve_remote_log_url()
    remote_enabled = bool(remote_url) and _parse_bool_env("NODE_LOGS_ENABLED", True)
    remote_level_name = (_get_env("NODE_LOGS_LEVEL") or _get_env("LOG_REMOTE_LEVEL") or "INFO").upper()
    remote_level_value = getattr(logging, remote_level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(
        min(
            level_value,
            file_level_value,
            remote_level_value if remote_enabled else logging.CRITICAL,
        )
    )

    if getattr(root, "_structured_logging_configured", False):
        for handler in root.handlers:
            if isinstance(handler, RotatingFileHandler):
                handler.setLevel(file_level_value)
            elif isinstance(handler, RemoteNDJSONLogHandler):
                handler.setLevel(remote_level_value)
            else:
                handler.setLevel(level_value)
        return

    log_file = _resolve_log_file()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = _parse_int_env("LOG_MAX_BYTES", DEFAULT_LOG_MAX_BYTES)
    backup_count = _parse_int_env("LOG_BACKUP_COUNT", DEFAULT_LOG_BACKUP_COUNT)

    run_id = _resolve_run_id()
    run_by = _resolve_run_by()
    node_id = _get_env("NODE_ID", "default-node") or "default-node"
    host = socket.gethostname()
    context_filter = RuntimeContextFilter(run_id, run_by, node_id, host)

    console_format = (
        "%(asctime)s %(levelname)-7s "
        "run=%(run_id)s node=%(node_id)s user=%(run_by)s "
        "[%(name)s] %(filename)s:%(lineno)d: %(message)s"
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level_value)
    console_handler.addFilter(context_filter)
    console_handler.setFormatter(logging.Formatter(console_format, "%Y-%m-%d %H:%M:%S"))

    file_handler = SafeRotatingFileHandler(
        str(log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        rollover_retry_interval=_parse_float_env("LOG_ROLLOVER_RETRY_INTERVAL", 30.0),
    )
    file_handler.setLevel(file_level_value)
    file_handler.addFilter(context_filter)
    file_handler.setFormatter(StructuredJsonFormatter())

    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    if remote_enabled:
        remote_handler = RemoteNDJSONLogHandler(
            remote_url,
            token=_resolve_remote_log_token(),
            batch_size=_parse_int_env("NODE_LOGS_BATCH_SIZE", DEFAULT_REMOTE_LOG_BATCH_SIZE),
            flush_interval=_parse_float_env(
                "NODE_LOGS_FLUSH_INTERVAL",
                DEFAULT_REMOTE_LOG_FLUSH_INTERVAL,
            ),
            timeout=_parse_float_env("NODE_LOGS_TIMEOUT", DEFAULT_REMOTE_LOG_TIMEOUT),
            queue_size=_parse_int_env("NODE_LOGS_QUEUE_SIZE", DEFAULT_REMOTE_LOG_QUEUE_SIZE),
        )
        remote_handler.setLevel(remote_level_value)
        remote_handler.addFilter(context_filter)
        remote_handler.setFormatter(RemoteNDJSONFormatter())
        root.addHandler(remote_handler)

    root._structured_logging_configured = True
    root._structured_log_file = str(log_file)
    root._remote_log_url = remote_url if remote_enabled else ""
