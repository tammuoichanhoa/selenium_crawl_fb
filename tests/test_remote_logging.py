import json
import logging

from src.utils import logging_setup


def test_remote_handler_posts_ndjson(monkeypatch):
    sent = []

    class Response:
        ok = True
        status_code = 200

    def fake_post(url, data, headers, timeout):
        sent.append(
            {
                "url": url,
                "data": data,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return Response()

    monkeypatch.setattr(logging_setup.requests, "post", fake_post)

    handler = logging_setup.RemoteNDJSONLogHandler(
        "https://example.test/node-logs",
        token="token-1",
        batch_size=1,
        flush_interval=0.1,
        timeout=0.1,
    )
    handler.setFormatter(logging_setup.RemoteNDJSONFormatter())
    record = logging.LogRecord(
        "main",
        logging.INFO,
        __file__,
        10,
        "line\n1",
        (),
        None,
    )
    record.run_id = "run-1"
    record.run_by = "tester"
    record.node_id = "node-A"
    record.host = "host-A"

    handler.emit(record)
    handler.close()

    assert sent
    assert sent[0]["url"] == "https://example.test/node-logs"
    assert sent[0]["headers"]["Authorization"] == "Bearer token-1"
    assert sent[0]["headers"]["Content-Type"] == "application/x-ndjson"

    lines = sent[0]["data"].decode("utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["level"] == "info"
    assert payload["ts"].endswith("Z")
    assert payload["message"] == "line\n1"
    assert payload["run_id"] == "run-1"
    assert payload["node_id"] == "node-A"
