"""Smoke test for tarn_cli."""

from pathlib import Path

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
    assert "LLM scorer  : off" in result.stdout
    get_settings.cache_clear()


def test_the_llm_scorer_shows_in_doctor_only_when_switched_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TARN_DEVICE", "cpu")
    monkeypatch.setenv("TARN_LLM_SCORER_ENABLED", "true")
    monkeypatch.setenv("TARN_LLM_ALLOW_IN_DEVELOPMENT", "true")
    monkeypatch.setenv("GROQ_API_KEYS", "a,b")
    get_settings.cache_clear()
    result = runner.invoke(app, ["doctor"])
    assert "LLM scorer  : on (2 keys; only colleges switched on are sent)" in result.stdout
    assert "a,b" not in result.stdout
    get_settings.cache_clear()


def test_tenants_llm_needs_an_operator_and_defaults_to_off() -> None:
    result = runner.invoke(app, ["tenants", "llm", "ACME"])
    assert result.exit_code != 0  # --operator is required: no anonymous switching
    help_text = runner.invoke(app, ["tenants", "llm", "--help"]).stdout
    assert "--on" in help_text and "--off" in help_text and "--operator" in help_text


def test_db_commands_are_listed() -> None:
    result = runner.invoke(app, ["db", "--help"])
    assert result.exit_code == 0
    for command in ("upgrade", "downgrade", "app-login"):
        assert command in result.stdout


def test_db_downgrade_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARN_ENV", "production")
    monkeypatch.setenv("TARN_KMS_KEY_REF", "gcp-kms:projects/p/locations/l/keyRings/r/cryptoKeys/k")
    monkeypatch.setenv("TARN_SECRETS_BACKEND", "gcp")
    monkeypatch.setenv("TARN_BLOB_BACKEND", "gcs")
    monkeypatch.setenv("TARN_MAILER", "smtp")
    monkeypatch.setenv("TARN_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("TARN_SMTP_FROM", "tarn@example.test")
    get_settings.cache_clear()
    result = runner.invoke(app, ["db", "downgrade", "base"])
    assert result.exit_code == 1
    assert "refusing" in result.stdout
    get_settings.cache_clear()


def test_seed_command_is_listed_and_documented() -> None:
    result = runner.invoke(app, ["seed", "--help"])
    assert result.exit_code == 0
    assert "development seed" in result.output.lower()


def test_ground_truth_and_benchmark_commands_are_listed() -> None:
    assert "prefill" in runner.invoke(app, ["truth", "--help"]).output
    assert "transcribe" in runner.invoke(app, ["truth", "--help"]).output
    assert "ocr" in runner.invoke(app, ["bench", "--help"]).output


def test_truth_status_counts_and_an_empty_set_is_refused(tmp_path: Path) -> None:
    from tarn_adapters.groundtruth.store import DirectoryGroundTruthStore
    from tarn_core.domain.common import Box
    from tarn_core.domain.groundtruth import CaptureType
    from tarn_core.domain.ocr import ContentClass
    from tarn_core.services.groundtruth import GroundTruthService, PrefillLine

    service = GroundTruthService(DirectoryGroundTruthStore(tmp_path))
    service.add_prefilled(
        page_id="x-p01",
        capture=CaptureType.PHONE_PHOTO,
        image=b"\xff\xd8",
        image_name="x-p01.jpg",
        width=100,
        height=100,
        lines=[
            PrefillLine(
                box=Box(x0=1, y0=1, x1=50, y1=20), text="a", content_class=ContentClass.PRINT
            )
        ],
    )
    service.record_correction(
        page_id="x-p01",
        box=Box(x0=1, y0=1, x1=50, y1=20),
        text="b",
        content_class=ContentClass.PRINT,
    )
    result = runner.invoke(app, ["truth", "status", str(tmp_path)])
    assert result.exit_code == 0
    assert "1 pages, 1 lines: 1 verified, 0 to check, 0 ignored" in result.output
    assert "phone_photo 1" in result.output and "print 1" in result.output
    empty = tmp_path / "empty"
    empty.mkdir()
    assert runner.invoke(app, ["bench", "ocr", str(empty)]).exit_code == 1
    assert runner.invoke(app, ["truth", "transcribe", str(empty)]).exit_code == 1


def test_page_numbers_are_parsed_and_checked() -> None:
    from tarn_cli.main import _page_numbers

    assert _page_numbers("3,5,7-9") == [3, 5, 7, 8, 9]
    for bad in ("0", "a", "", "5-"):
        with pytest.raises(Exception):  # noqa: B017  (typer.BadParameter)
            _page_numbers(bad)
