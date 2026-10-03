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


def test_db_commands_are_listed() -> None:
    result = runner.invoke(app, ["db", "--help"])
    assert result.exit_code == 0
    for command in ("upgrade", "downgrade", "app-login"):
        assert command in result.stdout


def test_db_downgrade_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARN_ENV", "production")
    monkeypatch.setenv("TARN_KMS_KEY_REF", "gcp-kms:projects/p/locations/l/keyRings/r/cryptoKeys/k")
    monkeypatch.setenv("TARN_SECRETS_BACKEND", "gcp")
    get_settings.cache_clear()
    result = runner.invoke(app, ["db", "downgrade", "base"])
    assert result.exit_code == 1
    assert "refusing" in result.stdout
    get_settings.cache_clear()


def test_seed_command_is_listed_and_documented() -> None:
    result = runner.invoke(app, ["seed", "--help"])
    assert result.exit_code == 0
    assert "development seed" in result.output.lower()
