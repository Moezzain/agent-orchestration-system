"""Multi-agent orchestration system with tool use, memory, and human-in-the-loop."""

__version__ = "0.1.0"


def health() -> dict[str, str]:
    """Smoke-test entrypoint proving the package imports and tooling runs."""
    return {"status": "ok", "version": __version__}
