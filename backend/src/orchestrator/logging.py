"""structlog setup: pretty console output in dev, JSON lines in prod."""

import logging

import structlog


def configure_logging(*, environment: str = "dev", log_level: str = "INFO") -> None:
    """Configure structlog for the whole process. Call once at startup."""
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if environment == "prod"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[log_level.upper()]
        ),
        # No file argument: resolve sys.stdout at call time, not configure time
        # (a captured stdout object can be closed later — e.g. pytest capsys)
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Named logger; call after configure_logging()."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
