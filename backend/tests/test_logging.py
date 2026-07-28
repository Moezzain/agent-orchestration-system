import json
from collections.abc import Iterator

import pytest
import structlog

from orchestrator.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    # These tests reconfigure global structlog state; restore defaults so
    # loggers created in other tests don't inherit a test-scoped config.
    yield
    structlog.reset_defaults()


def test_prod_logs_are_json_lines(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(environment="prod", log_level="INFO")

    get_logger("test").info("task_started", task_id="abc-123")

    line = capsys.readouterr().out.strip()
    payload = json.loads(line)
    assert payload["event"] == "task_started"
    assert payload["task_id"] == "abc-123"
    assert payload["level"] == "info"
    assert "timestamp" in payload


def test_log_level_filters_below_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(environment="prod", log_level="WARNING")

    logger = get_logger("test")
    logger.info("should_be_dropped")
    logger.warning("should_appear")

    out = capsys.readouterr().out
    assert "should_be_dropped" not in out
    assert "should_appear" in out
