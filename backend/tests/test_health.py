from orchestrator import __version__, health


def test_health_reports_ok() -> None:
    # Arrange / Act
    result = health()

    # Assert
    assert result == {"status": "ok", "version": __version__}
