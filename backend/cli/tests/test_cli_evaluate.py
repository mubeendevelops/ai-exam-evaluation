"""``tarn evaluate`` and ``tarn exams``: one booklet from a folder or stdin to result.json and a
draft sheet, with the image work faked and OCR scripted (the pipeline is the real one)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tarn_adapters.config import Settings
from tarn_adapters.stages import Stages
from tarn_adapters.testing import scripted_stages
from tarn_cli import evaluate as cli_evaluate
from tarn_cli.main import app
from tarn_core.ports.storage import BlobStore
from tarn_core.testing import fake_image, fake_pdf

runner = CliRunner()
BASE = ["--college", "DEMO_COM", "--exam", "QP-CI"]


@pytest.fixture(autouse=True)
def scripted(monkeypatch: pytest.MonkeyPatch) -> None:
    def build(settings: Settings, blobs: BlobStore) -> Stages:
        return scripted_stages(blobs)

    monkeypatch.setattr(cli_evaluate, "build_stages", build)


def booklet(folder: Path, *files: tuple[str, bytes]) -> Path:
    folder.mkdir()
    for name, data in files:
        (folder / name).write_bytes(data)
    return folder


def test_a_folder_becomes_result_json_and_a_draft_sheet(tmp_path: Path) -> None:
    folder = booklet(tmp_path / "b", ("pages.pdf", fake_pdf(1)), ("notes.txt", b"ignored"))
    result = runner.invoke(app, ["evaluate", str(folder), *BASE])
    assert result.exit_code == 0, result.output
    assert "status      : scored" in result.stdout
    assert "(suggestion, not approved)" in result.stdout
    data = json.loads((folder / "result" / "result.json").read_text())
    assert data["approved"] is False and data["status"] == "scored"
    assert {a["label"] for a in data["answers"]} == {"8", "9", "10"}
    assert float(data["total"]) > 0 and data["max_marks"] == "50"
    assert (folder / "result" / "result-sheet-draft.pdf").read_bytes().startswith(b"%PDF-")
    # The result names no student and holds no answer text.
    text = json.dumps(data)
    assert "DEMOC" not in text and "Nikhil" not in text and "living document" not in text


def test_the_manifest_in_the_folder_names_college_exam_and_student(tmp_path: Path) -> None:
    folder = booklet(
        tmp_path / "b",
        ("pages.pdf", fake_pdf(1)),
        ("booklet.json", json.dumps({"college": "demo_com", "exam": "17NC301"}).encode()),
    )
    out = tmp_path / "elsewhere"
    result = runner.invoke(app, ["evaluate", str(folder), "--out", str(out), "--usn", "DEMOC0003"])
    assert result.exit_code == 0, result.output
    assert "QP-IPR" in result.stderr  # the exam named by its course code
    assert json.loads((out / "result.json").read_text())["exam"].startswith("QP-IPR")


def test_a_pdf_arrives_on_stdin(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["evaluate", "-", *BASE, "--out", str(out)], input=fake_pdf(1, "piped")
    )
    assert result.exit_code == 0, result.output
    assert (out / "result.json").is_file()


def test_a_flagged_page_stops_the_run_unless_used_anyway(tmp_path: Path) -> None:
    folder = booklet(tmp_path / "b", ("p1.jpg", fake_image("blur")), ("p2.jpg", fake_image("ok")))
    stopped = runner.invoke(app, ["evaluate", str(folder), *BASE])
    assert stopped.exit_code == cli_evaluate.EXIT_RETAKE
    assert "retake page : 1 (blurry)" in stopped.stdout
    data = json.loads((folder / "result" / "result.json").read_text())
    assert data["status"] == "needs_retake" and data["answers"] == []
    assert not (folder / "result" / "result-sheet-draft.pdf").exists()

    go_on = runner.invoke(app, ["evaluate", str(folder), *BASE, "--use-anyway"])
    assert go_on.exit_code == 0, go_on.output
    assert json.loads((folder / "result" / "result.json").read_text())["status"] == "scored"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "name the college"),
        (["--college", "NOPE", "--exam", "QP-CI"], "unknown college 'NOPE': DEMO_ENG, DEMO_COM"),
        (["--college", "DEMO_COM"], "name the exam"),
        (["--college", "DEMO_COM", "--exam", "QP"], "several exams match 'QP'"),
        (["--college", "DEMO_COM", "--exam", "zzz"], "no exam matches 'zzz'"),
        ([*BASE, "--usn", "NOBODY"], "no student with USN 'NOBODY'"),
    ],
)
def test_a_vague_or_unknown_choice_is_an_error_not_a_guess(
    tmp_path: Path, args: list[str], message: str
) -> None:
    folder = booklet(tmp_path / "b", ("pages.pdf", fake_pdf(1)))
    result = runner.invoke(app, ["evaluate", str(folder), *args])
    assert result.exit_code == 2
    assert message in " ".join(result.stderr.split())
    assert not (folder / "result").exists()


def test_a_bad_folder_or_file_is_reported_without_a_traceback(tmp_path: Path) -> None:
    missing = runner.invoke(app, ["evaluate", str(tmp_path / "nope"), *BASE])
    assert missing.exit_code == 1 and "error:" in missing.stderr
    folder = booklet(tmp_path / "b", ("pages.pdf", b"this is not a pdf"))
    garbage = runner.invoke(app, ["evaluate", str(folder), *BASE])
    assert garbage.exit_code == 1 and "PDF" in garbage.stderr


def test_a_failing_stage_ends_the_booklet_and_names_the_error_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(settings: Settings, blobs: BlobStore) -> Stages:
        stages = scripted_stages(blobs)
        stages.splitter = _Exploding()  # type: ignore[assignment]
        return stages

    monkeypatch.setattr(cli_evaluate, "build_stages", broken)
    folder = booklet(tmp_path / "b", ("pages.pdf", fake_pdf(1)))
    result = runner.invoke(app, ["evaluate", str(folder), *BASE])
    assert result.exit_code == 1
    assert "stage error : RuntimeError" in result.stdout
    assert "secret detail" not in result.output  # never the message: it can echo text
    data = json.loads((folder / "result" / "result.json").read_text())
    assert data["status"] == "failed" and data["errors"] == ["RuntimeError"]


class _Exploding:
    def split(self, data: bytes, media_type: str, max_pages: int) -> list[object]:
        raise RuntimeError("secret detail")


def test_exams_lists_the_colleges_students_and_papers() -> None:
    result = runner.invoke(app, ["exams"])
    assert result.exit_code == 0
    assert "DEMO_COM" in result.stdout and "DEMOC0001" in result.stdout
    assert "QP-CI" in result.stdout and "50 marks" in result.stdout
