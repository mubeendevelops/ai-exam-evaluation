"""Smoke test for tarn_cli."""

import pytest
from typer.testing import CliRunner

from tarn_adapters.config import get_settings
from tarn_cli import __version__
from tarn_cli.main import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_doctor_reports_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARN_DEVICE", "cpu")
    get_settings.cache_clear()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "device      : cpu" in result.stdout
    assert "cloud OCR   : disabled" in result.stdout
    get_settings.cache_clear()
