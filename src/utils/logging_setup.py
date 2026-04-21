"""Structured logging configuration helpers."""

from __future__ import annotations

import getpass
import json
import logging
import os
import socket
import sys
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional


DEFAULT_LOG_DIR = Path("logs") / "logs"
DEFAULT_LOG_FILE = DEFAULT_LOG_DIR / "crawl.jsonl"
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 10

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
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _resolve_log_file() -> Path:
    raw = os.environ.get("LOG_FILE")
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_LOG_FILE


def _resolve_run_id() -> str:
    value = os.environ.get("RUN_ID") or os.environ.get("CRAWL_RUN_ID")
    if value:
        return value.strip()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{run_id}-{uuid.uuid4().hex[:8]}"


def _resolve_run_by() -> str:
    return (
        os.environ.get("LOG_RUN_BY")
        or os.environ.get("RUN_BY")
        or os.environ.get("USERNAME")
        or os.environ.get("USER")
        or getpass.getuser()
        or "unknown"
    )


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


def setup_logging(level: Optional[str] = None) -> None:
    """Configure root logging with console output and rotating JSONL file logs."""
    level_name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    level_value = getattr(logging, level_name, logging.INFO)
    file_level_name = (os.environ.get("LOG_FILE_LEVEL") or "DEBUG").upper()
    file_level_value = getattr(logging, file_level_name, logging.DEBUG)

    root = logging.getLogger()
    root.setLevel(min(level_value, file_level_value))

    if getattr(root, "_structured_logging_configured", False):
        for handler in root.handlers:
            if isinstance(handler, RotatingFileHandler):
                handler.setLevel(file_level_value)
            else:
                handler.setLevel(level_value)
        return

    log_file = _resolve_log_file()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = _parse_int_env("LOG_MAX_BYTES", DEFAULT_LOG_MAX_BYTES)
    backup_count = _parse_int_env("LOG_BACKUP_COUNT", DEFAULT_LOG_BACKUP_COUNT)

    run_id = _resolve_run_id()
    run_by = _resolve_run_by()
    node_id = os.environ.get("NODE_ID", "default-node")
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

    file_handler = RotatingFileHandler(
        str(log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level_value)
    file_handler.addFilter(context_filter)
    file_handler.setFormatter(StructuredJsonFormatter())

    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    root._structured_logging_configured = True
    root._structured_log_file = str(log_file)
