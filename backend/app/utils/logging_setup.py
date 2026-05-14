"""Centralised logging configuration.

The design doc enumerates the layout for ``data/logs/backend/``:

- ``app.log``        — every logger, every level (subject to LOG_LEVEL).
- ``error.log``      — WARNING+ only; plain text, easier to eyeball.
- ``access.log``     — HTTP access records, JSON.
- ``job.log``        — executor / scheduler / lifecycle.
- ``adapter.log``    — upstream calls (``txt2img.adapter.*``).
- ``auth.log``       — login / token / captcha.
- ``client-error.log`` — frontend reports via /api/client-logs.

Each file is rotated daily (``TimedRotatingFileHandler``); the previous
day's file is gzipped via ``rotator``. Anything older than the retain
window is removed by the handler itself (``backupCount``).

Call :func:`configure_logging` once, from the FastAPI lifespan, right
after ``get_settings()``. Re-calling it is a no-op (the function is
idempotent — handlers already installed are not duplicated).

The module also exposes :class:`RedactFilter` and
:class:`RequestContextFilter`, both of which can be reused in tests that
need to assert on a record's resolved fields.
"""

from __future__ import annotations

import gzip
import json
import logging
import logging.handlers
import os
import random
import re
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.config import Settings
from app.utils import log_context


# Loggers under these names route to job.log in addition to app.log.
_JOB_LOG_PREFIXES = (
    "txt2img.executor",
    "txt2img.scheduler",
    "txt2img.job_lifecycle",
    "txt2img.breaker",
    "txt2img.quota",
)

# Loggers under these names route to adapter.log.
_ADAPTER_LOG_PREFIXES = ("txt2img.adapter",)

# Loggers under these names route to auth.log.
_AUTH_LOG_PREFIXES = ("txt2img.auth", "txt2img.deps.auth")

# Logger name for the access record produced by the middleware.
ACCESS_LOGGER_NAME = "txt2img.access"

# Logger name for inbound frontend reports.
CLIENT_LOGGER_NAME = "txt2img.client"


_RESERVED_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        # Our own context fields, surfaced separately:
        "request_id",
        "user_id",
        "job_id",
        "client_ip",
    }
)


# Used by RedactFilter to scrub bearer / api-key patterns inside formatted
# message strings. Mirrors :mod:`app.utils.redact` but is intentionally
# kept lightweight so the filter never imports the JSON-shaped helpers.
_INLINE_TOKEN_RE = re.compile(
    r"(Authorization\s*:\s*[^\r\n,;]+"
    r"|Bearer\s+[A-Za-z0-9._\-+/=]{8,}"
    r"|sk-[A-Za-z0-9_\-]{16,}"
    r"|eyJ[A-Za-z0-9._\-]{20,})",
    re.IGNORECASE,
)


class RequestContextFilter(logging.Filter):
    """Inject the per-request ContextVars onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.request_id = (
            getattr(record, "request_id", None) or log_context.get_request_id()
        )
        record.user_id = (
            getattr(record, "user_id", None) or log_context.get_user_id()
        )
        record.job_id = (
            getattr(record, "job_id", None) or log_context.get_job_id()
        )
        record.client_ip = (
            getattr(record, "client_ip", None) or log_context.get_client_ip()
        )
        return True


class RedactFilter(logging.Filter):
    """Scrub sensitive substrings from every record.

    Three layers:

    1. Scan ``record.args`` (positional %-format args) for dicts and
       redact any sensitive keys.
    2. Walk extra-passed kwargs (anything :func:`logging.Logger._log`
       attached to the record beyond the standard attributes) and
       redact strings / dicts.
    3. Regex-strip bearer / api-key style substrings from the rendered
       message itself.

    The filter never raises — anything unexpected becomes a literal
    "[REDACTED]" replacement to keep the logger pipeline robust.
    """

    def __init__(self, sensitive_keys: Iterable[str]) -> None:
        super().__init__()
        self._sensitive = {k.lower() for k in sensitive_keys}

    def _scrub_dict(self, obj: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in self._sensitive:
                out[k] = "[REDACTED]"
            else:
                out[k] = self._scrub_value(v)
        return out

    def _scrub_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return self._scrub_dict(value)
        if isinstance(value, (list, tuple)):
            cleaned = [self._scrub_value(v) for v in value]
            return type(value)(cleaned) if isinstance(value, tuple) else cleaned
        if isinstance(value, str):
            return _INLINE_TOKEN_RE.sub("[REDACTED]", value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        try:
            if isinstance(record.args, dict):
                record.args = self._scrub_dict(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(self._scrub_value(a) for a in record.args)

            # Walk any "extra" attributes attached to the record beyond
            # the standard ones; LogRecord stores them as plain attrs.
            for attr in list(vars(record).keys()):
                if attr in _RESERVED_FIELDS or attr.startswith("_"):
                    continue
                val = getattr(record, attr, None)
                if isinstance(val, dict):
                    setattr(record, attr, self._scrub_dict(val))
                elif isinstance(val, str):
                    if attr.lower() in self._sensitive:
                        setattr(record, attr, "[REDACTED]")
                    else:
                        setattr(record, attr, _INLINE_TOKEN_RE.sub("[REDACTED]", val))

            # Finally scrub the raw msg string. We do this rather than
            # rewriting ``record.message`` because formatters call
            # ``record.getMessage()`` which renders msg % args fresh.
            if isinstance(record.msg, str):
                record.msg = _INLINE_TOKEN_RE.sub("[REDACTED]", record.msg)
        except Exception:  # pragma: no cover — never let the filter crash
            pass
        return True


class DebugSamplingFilter(logging.Filter):
    """Drop a configurable fraction of DEBUG records.

    ``rate=0.0`` means DEBUG is suppressed entirely; ``rate=1.0`` keeps
    every record. Anything ≥ INFO bypasses the sampler.
    """

    def __init__(self, rate: float) -> None:
        super().__init__()
        self._rate = max(0.0, min(1.0, float(rate)))

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        if record.levelno >= logging.INFO:
            return True
        if self._rate <= 0.0:
            return False
        if self._rate >= 1.0:
            return True
        return random.random() < self._rate


class _PrefixFilter(logging.Filter):
    """Route only records whose logger name starts with one of ``prefixes``."""

    def __init__(self, prefixes: tuple[str, ...]) -> None:
        super().__init__()
        self._prefixes = prefixes

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        name = record.name or ""
        return any(name == p or name.startswith(p + ".") or name == p for p in self._prefixes)


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record.

    Top-level fields: ``ts``, ``level``, ``logger``, ``msg``, plus our
    context-var injected ``request_id`` / ``user_id`` / ``job_id`` /
    ``client_ip`` / ``pid`` / ``thread``. Anything else attached to the
    record via ``logger.info(..., extra={...})`` is folded into an
    ``extra`` sub-object.
    """

    def __init__(self, *, include_traceback: bool = True) -> None:
        super().__init__()
        self._include_traceback = include_traceback

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        # ``record.getMessage()`` applies %-format on msg/args once.
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)

        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": message,
            "pid": record.process,
            "thread": record.threadName,
        }

        request_id = getattr(record, "request_id", None)
        user_id = getattr(record, "user_id", None)
        job_id = getattr(record, "job_id", None)
        client_ip = getattr(record, "client_ip", None)
        if request_id:
            payload["request_id"] = request_id
        if user_id:
            payload["user_id"] = user_id
        if job_id:
            payload["job_id"] = job_id
        if client_ip:
            payload["client_ip"] = client_ip

        extras: dict[str, Any] = {}
        for attr, val in vars(record).items():
            if attr in _RESERVED_FIELDS or attr.startswith("_"):
                continue
            extras[attr] = _jsonify(val)
        if extras:
            payload["extra"] = extras

        if record.exc_info and self._include_traceback:
            payload["exc"] = "".join(
                traceback.format_exception(*record.exc_info)
            ).rstrip()
        elif record.exc_text and self._include_traceback:
            payload["exc"] = record.exc_text

        try:
            return json.dumps(payload, ensure_ascii=False, default=_jsonify)
        except (TypeError, ValueError):
            # As a last resort emit a minimal record; never crash the
            # logging pipeline because a custom object is unhashable etc.
            return json.dumps(
                {
                    "ts": payload["ts"],
                    "level": payload["level"],
                    "logger": payload["logger"],
                    "msg": str(message),
                },
                ensure_ascii=False,
            )


def _jsonify(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonify(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return repr(value)
    return repr(value)


class _GzipNamer:
    """TimedRotatingFileHandler hook: rotated files get a ``.gz`` suffix."""

    def __call__(self, default_name: str) -> str:
        if default_name.endswith(".gz"):
            return default_name
        return default_name + ".gz"


def _gzip_rotator(source: str, dest: str) -> None:
    """Compress the rolled-out file into ``dest`` (which ends in .gz)."""
    if not os.path.exists(source):
        return
    try:
        with open(source, "rb") as src_fp, gzip.open(dest, "wb") as dst_fp:
            shutil.copyfileobj(src_fp, dst_fp)
    finally:
        try:
            os.remove(source)
        except OSError:
            pass


class _SafeTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Variant that never propagates a disk-side error up the logger chain."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            super().emit(record)
        except Exception:
            # Last-resort: write to stderr so the operator sees something
            # but the request handler is never blocked by a full disk.
            try:
                sys.stderr.write(
                    f"[txt2img-logging] file emit failed: {record.name} {record.levelname}\n"
                )
            except Exception:
                pass


_INSTALLED = False
_INSTALL_LOCK = threading.Lock()


def configure_logging(settings: Settings) -> None:
    """Install the project-wide logging handlers.

    Safe to call multiple times — subsequent invocations short-circuit
    and leave the existing configuration in place. Tests that need a
    fresh stack should call :func:`reset_logging_for_tests` first.
    """
    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        _install(settings)
        _INSTALLED = True


def reset_logging_for_tests() -> None:
    """Tear down all txt2img.* handlers so a test can install a fresh stack."""
    global _INSTALLED
    with _INSTALL_LOCK:
        for name in list(logging.Logger.manager.loggerDict.keys()):
            if name == "txt2img" or name.startswith("txt2img."):
                lg = logging.getLogger(name)
                for h in list(lg.handlers):
                    lg.removeHandler(h)
                    try:
                        h.close()
                    except Exception:
                        pass
                lg.propagate = True
                # pytest's logging plugin toggles ``logger.disabled``
                # between tests; clear it so configure_logging's fresh
                # handlers actually receive records.
                lg.disabled = False
        root = logging.getLogger()
        for h in list(root.handlers):
            # Only strip handlers we installed (tagged via attr below).
            if getattr(h, "_txt2img_installed", False):
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            for h in list(lg.handlers):
                if getattr(h, "_txt2img_installed", False):
                    lg.removeHandler(h)
                    try:
                        h.close()
                    except Exception:
                        pass
            lg.propagate = True
            lg.disabled = False
        _INSTALLED = False


def _ensure_log_dir(path: str) -> Path:
    """Make sure ``path`` exists; fall back to ``/tmp/txt2img-logs`` on failure."""
    candidate = Path(path)
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        # Probe writability.
        probe = candidate / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return candidate
    except OSError:
        # Last-resort fallback — operators should look at LOG_DIR.
        fallback = Path("/tmp/txt2img-logs")
        fallback.mkdir(parents=True, exist_ok=True)
        sys.stderr.write(
            f"[txt2img-logging] LOG_DIR={path} unwritable; falling back to {fallback}\n"
        )
        return fallback


def _make_file_handler(
    *,
    path: Path,
    when: str,
    backup_count: int,
    level: int,
    formatter: logging.Formatter,
    filters: list[logging.Filter],
) -> logging.Handler:
    handler = _SafeTimedRotatingFileHandler(
        str(path),
        when=when,
        backupCount=backup_count,
        encoding="utf-8",
        utc=True,
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    for flt in filters:
        handler.addFilter(flt)
    handler.namer = _GzipNamer()
    handler.rotator = _gzip_rotator
    handler._txt2img_installed = True  # type: ignore[attr-defined]
    return handler


def _make_stdout_handler(
    *,
    level: int,
    formatter: logging.Formatter,
    filters: list[logging.Filter],
) -> logging.Handler:
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    for flt in filters:
        handler.addFilter(flt)
    handler._txt2img_installed = True  # type: ignore[attr-defined]
    return handler


def _level_for_settings(level_name: str) -> int:
    return getattr(logging, level_name.upper(), logging.INFO)


def _install(settings: Settings) -> None:
    log_dir = _ensure_log_dir(settings.LOG_DIR)
    level = _level_for_settings(settings.LOG_LEVEL)
    fmt = (settings.LOG_FORMAT or "json").lower()

    redact_filter = RedactFilter(settings.log_redact_fields_set)
    ctx_filter = RequestContextFilter()
    sampling_filter = DebugSamplingFilter(settings.LOG_SAMPLE_DEBUG)
    common_filters: list[logging.Filter] = [
        ctx_filter,
        redact_filter,
        sampling_filter,
    ]

    if fmt == "json":
        primary_formatter: logging.Formatter = JsonFormatter(
            include_traceback=settings.LOG_INCLUDE_TRACEBACK,
        )
    else:
        primary_formatter = logging.Formatter(
            fmt=(
                "%(asctime)s %(levelname)s %(name)s "
                "[req=%(request_id)s user=%(user_id)s job=%(job_id)s] %(message)s"
            ),
        )

    # Plain-text formatter for the human-eyeball error.log so operators
    # can ``tail -F`` without piping through jq.
    error_formatter = logging.Formatter(
        fmt=(
            "%(asctime)s %(levelname)-8s %(name)s "
            "[req=%(request_id)s user=%(user_id)s] %(message)s"
        ),
    )
    # Use UTC for the timestamps (matches the JSON formatter).
    logging.Formatter.converter = time.gmtime

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Strip any previously-installed file handlers — important when
    # ``configure_logging`` is invoked from a test that runs the
    # lifespan twice.
    for handler in list(root_logger.handlers):
        if getattr(handler, "_txt2img_installed", False):
            root_logger.removeHandler(handler)

    txt2img_root = logging.getLogger("txt2img")
    txt2img_root.setLevel(level)
    txt2img_root.propagate = False
    # pytest's logging plugin may have toggled ``disabled`` on us
    # between fixtures; reset it so records actually reach our handlers.
    txt2img_root.disabled = False

    # ------------- main app.log -------------
    app_handler = _make_file_handler(
        path=log_dir / "app.log",
        when=settings.LOG_ROTATE_WHEN,
        backup_count=settings.LOG_RETAIN_DAYS,
        level=level,
        formatter=primary_formatter,
        filters=common_filters,
    )
    txt2img_root.addHandler(app_handler)

    # ------------- error.log (WARNING+) -------------
    error_handler = _make_file_handler(
        path=log_dir / "error.log",
        when=settings.LOG_ROTATE_WHEN,
        backup_count=max(settings.LOG_RETAIN_DAYS, 60),
        level=logging.WARNING,
        formatter=error_formatter,
        filters=common_filters,
    )
    txt2img_root.addHandler(error_handler)

    # ------------- stdout (developer-friendly) -------------
    if settings.LOG_TO_STDOUT:
        stdout_formatter: logging.Formatter
        if fmt == "json":
            stdout_formatter = primary_formatter
        else:
            stdout_formatter = primary_formatter
        stdout_handler = _make_stdout_handler(
            level=level,
            formatter=stdout_formatter,
            filters=common_filters,
        )
        txt2img_root.addHandler(stdout_handler)
        # Mirror to root too so any third-party library (e.g. SQLAlchemy)
        # whose level we leave at WARNING still emits to the console.
        root_logger.addHandler(stdout_handler)

    # ------------- access.log -------------
    if settings.LOG_ACCESS_ENABLED:
        access_logger = logging.getLogger(ACCESS_LOGGER_NAME)
        access_logger.setLevel(logging.INFO)
        access_logger.propagate = True  # also reach app.log
        access_handler = _make_file_handler(
            path=log_dir / "access.log",
            when=settings.LOG_ROTATE_WHEN,
            backup_count=max(7, min(settings.LOG_RETAIN_DAYS, 14)),
            level=logging.INFO,
            formatter=primary_formatter,
            filters=common_filters,
        )
        access_logger.addHandler(access_handler)

    # ------------- job.log -------------
    job_handler = _make_file_handler(
        path=log_dir / "job.log",
        when=settings.LOG_ROTATE_WHEN,
        backup_count=settings.LOG_RETAIN_DAYS,
        level=logging.DEBUG if level <= logging.DEBUG else logging.INFO,
        formatter=primary_formatter,
        filters=common_filters + [_PrefixFilter(_JOB_LOG_PREFIXES)],
    )
    txt2img_root.addHandler(job_handler)

    # ------------- adapter.log -------------
    if settings.LOG_ADAPTER_ENABLED:
        adapter_handler = _make_file_handler(
            path=log_dir / "adapter.log",
            when=settings.LOG_ROTATE_WHEN,
            backup_count=max(7, min(settings.LOG_RETAIN_DAYS, 14)),
            level=logging.DEBUG if level <= logging.DEBUG else logging.INFO,
            formatter=primary_formatter,
            filters=common_filters + [_PrefixFilter(_ADAPTER_LOG_PREFIXES)],
        )
        txt2img_root.addHandler(adapter_handler)

    # ------------- auth.log -------------
    auth_handler = _make_file_handler(
        path=log_dir / "auth.log",
        when=settings.LOG_ROTATE_WHEN,
        backup_count=max(settings.LOG_RETAIN_DAYS, 90),
        level=logging.INFO,
        formatter=primary_formatter,
        filters=common_filters + [_PrefixFilter(_AUTH_LOG_PREFIXES)],
    )
    txt2img_root.addHandler(auth_handler)

    # ------------- client-error.log (frontend reports) -------------
    if settings.LOG_CLIENT_LOGS_ENABLED:
        client_logger = logging.getLogger(CLIENT_LOGGER_NAME)
        client_logger.setLevel(logging.DEBUG)
        client_logger.propagate = True
        client_handler = _make_file_handler(
            path=log_dir / "client-error.log",
            when=settings.LOG_ROTATE_WHEN,
            backup_count=max(7, min(settings.LOG_RETAIN_DAYS, 14)),
            level=logging.DEBUG,
            formatter=primary_formatter,
            filters=common_filters,
        )
        client_logger.addHandler(client_handler)

    # ------------- uvicorn integration -------------
    # Route uvicorn.error onto our app.log and uvicorn.access onto access.log.
    # We can't share handler instances safely because each handler carries
    # filters that depend on the record name; instead we attach the same
    # file path but a fresh handler.
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.setLevel(level)
    uvicorn_error.handlers = []
    uvicorn_error.propagate = False
    uvicorn_error.addHandler(
        _make_file_handler(
            path=log_dir / "app.log",
            when=settings.LOG_ROTATE_WHEN,
            backup_count=settings.LOG_RETAIN_DAYS,
            level=level,
            formatter=primary_formatter,
            filters=common_filters,
        )
    )
    if settings.LOG_TO_STDOUT:
        uvicorn_error.addHandler(
            _make_stdout_handler(
                level=level,
                formatter=primary_formatter,
                filters=common_filters,
            )
        )

    uvicorn_access = logging.getLogger("uvicorn.access")
    # Our middleware emits its own access record; uvicorn's default
    # format is too sparse. Quiet uvicorn.access and rely on our line.
    uvicorn_access.setLevel(logging.WARNING)
    uvicorn_access.handlers = []
    uvicorn_access.propagate = False


def field_extra(**fields: Any) -> dict[str, dict[str, Any]]:
    """Convenience wrapper for ``logger.info(..., extra=field_extra(foo=1))``.

    ``logging`` requires extras to be flat dicts whose keys do not
    collide with reserved ``LogRecord`` attribute names; this helper
    namespaces them under ``extra=`` so the JSON formatter renders them
    under the ``extra`` sub-object without risk of collision.
    """
    return {"extra_fields": fields}


def merge_extra(base: Mapping[str, Any] | None, **overrides: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if base:
        out.update(base)
    out.update(overrides)
    return out


__all__ = (
    "configure_logging",
    "reset_logging_for_tests",
    "RequestContextFilter",
    "RedactFilter",
    "DebugSamplingFilter",
    "JsonFormatter",
    "ACCESS_LOGGER_NAME",
    "CLIENT_LOGGER_NAME",
)
