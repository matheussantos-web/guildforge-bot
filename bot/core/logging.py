"""Configuração de logging estruturado do bot.

Em produção (``ENVIRONMENT=production``) os logs são emitidos em JSON — uma
linha por registro — para agregação por ferramentas como Loki, CloudWatch ou
Datadog. Em desenvolvimento usa formatação legível por humanos.

Uso básico:

    from bot.config import ENVIRONMENT
    from bot.core.logging import setup_logging

    setup_logging(environment=ENVIRONMENT, level="INFO")

Ou, de forma preguiçosa (sem env), :func:`setup_logging_default`.

Os JSON incluem campos escalares (timestamp, level, logger, mensagem,
nomes de módulo/linha e a duração em ``ms``). Exceções são serializadas como
``exc_info``/``exc_text`` quando presentes, sem quebrar a linha JSON.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
from typing import Any
from zoneinfo import ZoneInfo

_DEFAULT_TZ = ZoneInfo("UTC")


def _iso(ts: float) -> str:
    dt = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc)
    return dt.astimezone(_DEFAULT_TZ).isoformat(timespec="milliseconds")


def _to_scalar(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


class JsonFormatter(logging.Formatter):
    """Emitter de logs em JSON de linha única."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _iso(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
            payload["exc_info"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if extra:
            for key in sorted(extra):
                payload[key] = _to_scalar(extra[key])
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(environment: str = "development", level: str = "INFO") -> None:
    """Configura a raiz do logging e os handlers globais do bot."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if environment == "production":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
    handler.setFormatter(formatter)
    root.addHandler(handler)
