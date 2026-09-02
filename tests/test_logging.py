"""Testes do logging estruturado (JSON em produção, humanizado em dev)."""

from __future__ import annotations

import json
import logging

from bot.core.logging import JsonFormatter, setup_logging


def _make_record(message: str = "hello", level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="test.logger",
        level=level,
        pathname="mod.py",
        lineno=42,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_json_formatter_outputs_valid_single_line() -> None:
    out = JsonFormatter().format(_make_record("hello mundo"))
    parsed = json.loads(out)
    assert parsed["level"] == "INFO"
    assert parsed["message"] == "hello mundo"
    assert parsed["logger"] == "test.logger"
    assert parsed["line"] == 42


def test_json_formatter_includes_exception() -> None:
    record = _make_record("boom", logging.ERROR)
    try:
        raise ValueError("x")
    except ValueError:
        record.exc_info = logging.sys.exc_info()
    record.exc_text = None
    out = JsonFormatter().format(record)
    parsed = json.loads(out)
    assert parsed["exc_type"] == "ValueError"
    assert "ValueError" in parsed["exc_info"]


def test_json_formatter_includes_extra_fields() -> None:
    record = _make_record("com extra")
    record.extra_fields = {"guild_id": 123, "session_id": 1}
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["guild_id"] == 123
    assert parsed["session_id"] == 1


def test_setup_logging_production_uses_json_formatter() -> None:
    setup_logging(environment="production", level="INFO")
    formatter = _root_handler_formatter()
    assert isinstance(formatter, JsonFormatter)


def test_setup_logging_development_uses_text_formatter() -> None:
    setup_logging(environment="development", level="INFO")
    formatter = _root_handler_formatter()
    assert not isinstance(formatter, JsonFormatter)


def test_production_log_row_is_valid_json(capsys) -> None:
    setup_logging(environment="production", level="INFO")
    logging.getLogger("prod.test").info("mensagem-prod-json")
    captured = capsys.readouterr().out.strip().splitlines()[-1]
    parsed = json.loads(captured)
    assert parsed["message"] == "mensagem-prod-json"


def _root_handler_formatter() -> logging.Formatter:
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            return handler.formatter
    raise AssertionError("Nenhum StreamHandler encontrado na raiz")
