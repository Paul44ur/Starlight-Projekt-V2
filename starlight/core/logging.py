"""Structured JSON logs with rotation, in three places:

* ``logs/starlight.log``: everything (core and modules)
* ``logs/modules/<name>.log``: one module only
* ``logs/audit.log``: every action the AI takes, and every kill-switch trigger
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog
from structlog.typing import EventDict, WrappedLogger

ROOT_LOGGER = "starlight"
AUDIT_LOGGER = "starlight.audit"
MODULE_PREFIX = "starlight.module."

_SECRET_KEY = re.compile(
    r"(^|[_-])(pass(word)?|passwd|secret|token|api[_-]?key|cookies?|credentials?"
    r"|authorization|private[_-]?key)($|[_-])",
    re.IGNORECASE,
)
REDACTED = "***"


_SECRET_VALUES: set[str] = set()


def add_secret_values(values: Any) -> None:
    """Known secrets (from the vault): masked wherever they appear in a log line."""
    _SECRET_VALUES.update(v for v in values if isinstance(v, str) and len(v) >= 6)


def _mask_known(text: str) -> str:
    for secret in _SECRET_VALUES:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


def redact(value: Any) -> Any:
    """Mask values whose key looks like a secret, and known secret values, recursively."""
    if isinstance(value, str):
        return _mask_known(value) if _SECRET_VALUES else value
    if isinstance(value, dict):
        return {
            k: REDACTED if isinstance(k, str) and _SECRET_KEY.search(k) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def _redact_processor(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    return redact(event_dict)  # type: ignore[no-any-return]


class _State:
    logs_dir: Path | None = None
    max_bytes: int = 5_000_000
    backups: int = 5
    handlers: dict[str, logging.Handler] = {}  # noqa: RUF012


def _formatter() -> logging.Formatter:
    shared: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(default=str),
        ],
    )


class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotation that never loses lines. On Windows another Starlight process (the
    background core, a CLI command) may hold the file open, so renaming it fails; then
    keep writing to the current file and try again a minute later."""

    retry_after = 60.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._next_try = 0.0

    def shouldRollover(self, record: logging.LogRecord) -> int:
        if time.monotonic() < self._next_try:
            return 0
        return super().shouldRollover(record)

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError:
            self._next_try = time.monotonic() + self.retry_after
            if self.stream is None:
                self.stream = self._open()  # back to appending to the current file


def _file_handler(path: Path) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = SafeRotatingFileHandler(
        path, maxBytes=_State.max_bytes, backupCount=_State.backups, encoding="utf-8"
    )
    handler.setFormatter(_formatter())
    return handler


def _attach(logger_name: str, path: Path, propagate: bool) -> None:
    logger = logging.getLogger(logger_name)
    old = _State.handlers.pop(logger_name, None)
    if old is not None:
        logger.removeHandler(old)
        old.close()
    handler = _file_handler(path)
    logger.addHandler(handler)
    logger.propagate = propagate
    _State.handlers[logger_name] = handler


def setup_logging(
    logs_dir: Path, level: str = "INFO", max_bytes: int = 5_000_000, backups: int = 5
) -> None:
    """Configure logging. Safe to call again (e.g. in tests): old handlers are closed."""
    shutdown_logging()
    _State.logs_dir = logs_dir
    _State.max_bytes = max_bytes
    _State.backups = backups

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    logging.getLogger(ROOT_LOGGER).setLevel(level.upper())
    _attach(ROOT_LOGGER, logs_dir / "starlight.log", propagate=False)
    _attach(AUDIT_LOGGER, logs_dir / "audit.log", propagate=False)
    logging.getLogger(AUDIT_LOGGER).setLevel(logging.INFO)


def shutdown_logging() -> None:
    for name, handler in list(_State.handlers.items()):
        logging.getLogger(name).removeHandler(handler)
        handler.close()
    _State.handlers.clear()


def get_logger(name: str = "core", **context: Any) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(f"{ROOT_LOGGER}.{name}")
    return logger.bind(**context) if context else logger


def module_logger(module: str) -> structlog.stdlib.BoundLogger:
    """Logger for a module; writes to logs/modules/<module>.log and the main log."""
    name = MODULE_PREFIX + module
    if _State.logs_dir is not None and name not in _State.handlers:
        _attach(name, _State.logs_dir / "modules" / f"{module}.log", propagate=True)
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger.bind(module=module)


class AuditLog:
    """Append-only record of actions the AI takes. Secrets are redacted."""

    def __init__(self) -> None:
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger(AUDIT_LOGGER)

    def record(self, action: str, **fields: Any) -> None:
        self._logger.info(action, **fields)


def recent_errors(
    logs_dir: Path, limit: int = 20, include_warnings: bool = True
) -> list[dict[str, Any]]:
    """The most recent problems from logs/starlight.log, in plain form: when, how bad,
    where, what, and the underlying cause. For `starlight logs show` and the chat."""
    import json

    path = logs_dir / "starlight.log"
    if not path.is_file():
        return []
    wanted = {"error", "critical"} | ({"warning"} if include_warnings else set())
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("level") not in wanted:
            continue
        where = str(entry.get("logger", "")).removeprefix("starlight.")
        for key in ("module", "tool"):
            if entry.get(key):
                where = f"{where} ({entry[key]})" if where else str(entry[key])
                break
        detail = entry.get("error") or entry.get("reason") or ""
        cause = ""
        trace = entry.get("exception")
        if isinstance(trace, str) and trace.strip():
            lines = [ln for ln in trace.strip().splitlines() if ln.strip()]
            cause = lines[-1] if lines else ""
        out.append(
            {
                "time": _local_time(str(entry.get("timestamp", ""))),
                "level": entry.get("level"),
                "where": where,
                "what": str(entry.get("event", "")),
                "detail": str(detail),
                "cause": cause,
            }
        )
    return out[-limit:] if limit > 0 else []


def _local_time(stamp: str) -> str:
    """A log timestamp (UTC) as this PC's local time, the way the user and the model read
    times; anything unreadable is shown as it is."""
    try:
        moment = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp[:19].replace("T", " ")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def format_errors(errors: list[dict[str, Any]]) -> str:
    """Render recent_errors() as short, readable lines."""
    if not errors:
        return "No recent errors or warnings."
    mark = {"warning": "warn ", "error": "ERROR", "critical": "CRIT "}
    lines = []
    for e in errors:
        head = f"{e['time']}  {mark.get(e['level'], e['level'])}  {e['where']}: {e['what']}"
        lines.append(head)
        if e["detail"] and e["detail"] not in e["what"]:
            lines.append(f"    {e['detail']}")
        if e["cause"]:
            lines.append(f"    cause: {e['cause']}")
    return "\n".join(lines)
