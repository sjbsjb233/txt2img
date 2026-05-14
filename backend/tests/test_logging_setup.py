"""Tests for ``app.utils.logging_setup`` and the ``log_context`` glue.

The fixtures here avoid the heavy ``seeded_app`` lifespan because we
only care about the file output of :func:`configure_logging`. Each test
gets its own temporary log directory so handlers from different tests
don't interleave.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import threading
from pathlib import Path

import pytest

from app.config import Settings
from app.utils import log_context
from app.utils.logging_setup import (
    ACCESS_LOGGER_NAME,
    CLIENT_LOGGER_NAME,
    JsonFormatter,
    RedactFilter,
    RequestContextFilter,
    configure_logging,
    reset_logging_for_tests,
)


def _make_settings(
    *,
    log_dir: Path,
    log_format: str = "json",
    log_level: str = "DEBUG",
    log_to_stdout: bool = False,
) -> Settings:
    """Build a Settings instance bypassing env vars.

    Settings reads env vars by default. The test environment already
    sets JWT_SECRET / ADMIN_PASSWORD via conftest; we just override the
    log knobs we need.
    """
    os.environ["JWT_SECRET"] = "test_secret_at_least_16_chars_long_value"
    os.environ["ADMIN_PASSWORD"] = "test-admin-password"
    settings = Settings(  # type: ignore[call-arg]
        JWT_SECRET="test_secret_at_least_16_chars_long_value",
        ADMIN_PASSWORD="test-admin-password",
        LOG_DIR=str(log_dir),
        LOG_FORMAT=log_format,
        LOG_LEVEL=log_level,
        LOG_TO_STDOUT=log_to_stdout,
        LOG_RETAIN_DAYS=2,
        LOG_REQUEST_BODY_MAX_BYTES=0,
        LOG_ACCESS_ENABLED=True,
        LOG_ADAPTER_ENABLED=True,
        LOG_CLIENT_LOGS_ENABLED=True,
        LOG_SAMPLE_DEBUG=1.0,
    )
    return settings


@pytest.fixture(autouse=True)
def _clean_logging() -> None:
    """Reset between tests so handlers don't accumulate."""
    reset_logging_for_tests()
    yield
    reset_logging_for_tests()


def _flush_all() -> None:
    for name in list(logging.Logger.manager.loggerDict.keys()):
        lg = logging.getLogger(name)
        for h in lg.handlers:
            try:
                h.flush()
            except Exception:
                pass


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_configure_logging_writes_app_log(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path)
    configure_logging(settings)
    logging.getLogger("txt2img").info("hello world")
    _flush_all()

    contents = _read(tmp_path / "app.log")
    assert contents, "app.log should exist after configure_logging"
    record = json.loads(contents.splitlines()[-1])
    assert record["msg"] == "hello world"
    assert record["logger"] == "txt2img"
    assert record["level"] == "INFO"


def test_request_context_filter_injects_request_id(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path)
    configure_logging(settings)
    log_context.set_request_id("rid-xyz")
    log_context.set_user_id("u_test")
    try:
        logging.getLogger("txt2img.auth").info("login attempted")
        _flush_all()
    finally:
        log_context.set_request_id(None)
        log_context.set_user_id(None)

    record = json.loads(_read(tmp_path / "app.log").splitlines()[-1])
    assert record["request_id"] == "rid-xyz"
    assert record["user_id"] == "u_test"


def test_redact_filter_scrubs_authorization_header(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path)
    configure_logging(settings)
    logging.getLogger("txt2img").warning(
        "incoming Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"
    )
    _flush_all()

    raw = _read(tmp_path / "app.log")
    assert "Bearer eyJ" not in raw
    assert "[REDACTED]" in raw


def test_redact_filter_strips_sensitive_dict_key(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path)
    configure_logging(settings)
    logging.getLogger("txt2img").warning(
        "config payload",
        extra={"api_key": "sk-abcdef1234567890abcdef", "user": "alice"},
    )
    _flush_all()

    record = json.loads(_read(tmp_path / "app.log").splitlines()[-1])
    extra = record.get("extra") or {}
    assert extra.get("api_key") == "[REDACTED]"
    assert extra.get("user") == "alice"


def test_redact_filter_does_not_mangle_safe_string(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path)
    configure_logging(settings)
    logging.getLogger("txt2img").info("user logged in: alice")
    _flush_all()

    raw = _read(tmp_path / "app.log")
    assert "user logged in: alice" in raw


def test_json_formatter_includes_traceback() -> None:
    formatter = JsonFormatter(include_traceback=True)
    record = logging.LogRecord(
        name="txt2img.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="boom",
        args=(),
        exc_info=None,
    )
    try:
        raise ValueError("boom!")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()
    out = formatter.format(record)
    parsed = json.loads(out)
    assert parsed["msg"] == "boom"
    assert "exc" in parsed
    assert "ValueError" in parsed["exc"]


def test_access_log_lands_in_access_file(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path)
    configure_logging(settings)
    logging.getLogger(ACCESS_LOGGER_NAME).info(
        "access",
        extra={
            "method": "GET",
            "path": "/api/health",
            "status": 200,
            "duration_ms": 1.2,
        },
    )
    _flush_all()
    raw = _read(tmp_path / "access.log")
    assert raw
    line = json.loads(raw.splitlines()[-1])
    assert line["logger"] == ACCESS_LOGGER_NAME
    extra = line.get("extra") or {}
    assert extra.get("path") == "/api/health"
    assert extra.get("status") == 200


def test_job_log_routes_executor_records(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path)
    configure_logging(settings)
    logging.getLogger("txt2img.executor").info("job done hash=abc")
    logging.getLogger("txt2img.auth").info("login success u=admin")
    _flush_all()

    job_log = _read(tmp_path / "job.log")
    assert "job done hash=abc" in job_log
    assert "login success" not in job_log

    auth_log = _read(tmp_path / "auth.log")
    assert "login success" in auth_log


def test_adapter_log_routes_adapter_namespace(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path)
    configure_logging(settings)
    logging.getLogger("txt2img.adapter.openai").info(
        "upstream call provider=p model=m"
    )
    _flush_all()

    log = _read(tmp_path / "adapter.log")
    assert "upstream call provider=p" in log


def test_client_log_routes_to_client_file(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path)
    configure_logging(settings)
    logging.getLogger(CLIENT_LOGGER_NAME).error(
        "client error", extra={"route": "/create"}
    )
    _flush_all()

    log = _read(tmp_path / "client-error.log")
    assert "client error" in log


def test_error_log_only_receives_warnings_plus(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path)
    configure_logging(settings)
    logging.getLogger("txt2img").info("not visible")
    logging.getLogger("txt2img").warning("definitely visible")
    _flush_all()

    err = _read(tmp_path / "error.log")
    assert "definitely visible" in err
    assert "not visible" not in err


def test_log_context_bind_restores_previous_values() -> None:
    log_context.set_request_id("outer")
    try:
        with log_context.bind(request_id="inner", user_id="u1"):
            assert log_context.get_request_id() == "inner"
            assert log_context.get_user_id() == "u1"
        assert log_context.get_request_id() == "outer"
        assert log_context.get_user_id() is None
    finally:
        log_context.set_request_id(None)
        log_context.set_user_id(None)


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path)
    configure_logging(settings)
    first_count = len(logging.getLogger("txt2img").handlers)
    configure_logging(settings)
    second_count = len(logging.getLogger("txt2img").handlers)
    assert first_count == second_count


def test_redact_filter_unit_handles_dict_args() -> None:
    f = RedactFilter({"api_key", "password"})
    # ``logging.LogRecord`` collapses a single-dict args tuple into the
    # dict itself (so ``%(api_key)s`` interpolation works). Our filter
    # has to handle both shapes.
    record = logging.LogRecord(
        name="txt2img.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="payload=%s",
        args=({"api_key": "secret-key-value", "ok": True},),
        exc_info=None,
    )
    f.filter(record)
    if isinstance(record.args, dict):
        assert record.args["api_key"] == "[REDACTED]"
        assert record.args["ok"] is True
    else:
        assert record.args[0]["api_key"] == "[REDACTED]"


def test_request_context_filter_overrides_record_attrs() -> None:
    f = RequestContextFilter()
    log_context.set_request_id("rid")
    log_context.set_user_id("u")
    try:
        record = logging.LogRecord(
            name="txt2img.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="m",
            args=(),
            exc_info=None,
        )
        f.filter(record)
        assert record.request_id == "rid"
        assert record.user_id == "u"
    finally:
        log_context.set_request_id(None)
        log_context.set_user_id(None)


def test_text_format_still_renders(tmp_path: Path) -> None:
    settings = _make_settings(log_dir=tmp_path, log_format="text")
    configure_logging(settings)
    logging.getLogger("txt2img").info("plain text line")
    _flush_all()
    raw = _read(tmp_path / "app.log")
    assert "plain text line" in raw
    # text format keeps a leading timestamp, not JSON braces.
    assert not raw.startswith("{")
