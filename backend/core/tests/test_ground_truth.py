"""Ground-truth pages: the JSON-lines format, its refusals, the manifest it doubles as, and the
service that stores pre-fills, keeps a person's work and takes teacher corrections (P16)."""

import json
from uuid import uuid4

import pytest

from tarn_core.domain.common import Box
from tarn_core.domain.groundtruth import (
    CaptureType,
    TruthLine,
    TruthOrigin,
    TruthPage,
    TruthStatus,
)
from tarn_core.domain.ocr import ContentClass
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.services.groundtruth import (
    GroundTruthService,
    NewPage,
    PrefillLine,
    page_from_jsonl,
    page_to_jsonl,
)
from tarn_core.testing.groundtruth import MemoryGroundTruthStore

IMAGE = b"\xff\xd8page"
SECRET = "zyxwv unmistakable handwriting"


def box(x0: int = 10, y0: int = 10, x1: int = 400, y1: int = 60) -> Box:
    return Box(x0=x0, y0=y0, x1=x1, y1=y1)


def page(**changes: object) -> TruthPage:
    fields: dict[str, object] = {
        "id": "b-ci2-p04",
        "capture": CaptureType.SCANNING_APP,
        "image": "b-ci2-p04.jpg",
        "width": 1000,
        "height": 1400,
        "source": "B-CI2",
        "lines": (
            TruthLine(
                box=box(),
                text=SECRET,
                content_class=ContentClass.CURSIVE,
                status=TruthStatus.VERIFIED,
                prefill="zyxwv unmistakeable handwriting",
            ),
            TruthLine(box=box(10, 100, 400, 150), text="draft", content_class=ContentClass.PRINT),
        ),
    }
    fields.update(changes)
    return TruthPage(**fields)  # type: ignore[arg-type]


def service() -> tuple[GroundTruthService, MemoryGroundTruthStore]:
    store = MemoryGroundTruthStore()
    return GroundTruthService(store), store


# --- format -----------------------------------------------------------------------------------


def test_jsonl_round_trip_keeps_every_field() -> None:
    original = page(
        college_id=uuid4(),
        lines=(
            TruthLine(
                box=box(),
                text="é ü",
                content_class=ContentClass.NUMERIC,
                status=TruthStatus.VERIFIED,
                origin=TruthOrigin.TEACHER_CORRECTION,
                prefill="e u",
                region_id=uuid4(),
            ),
        ),
    )
    assert page_from_jsonl(page_to_jsonl(original)) == original


def test_each_record_is_self_contained_and_is_a_calibration_manifest_line() -> None:
    records = [json.loads(line) for line in page_to_jsonl(page()).splitlines()]
    assert len(records) == 2
    for record in records:
        # the manifest of `tarn ocr calibrate` (image, box, text, class) plus the benchmark's own
        assert {"image", "box", "text", "class", "capture", "page", "status"} <= set(record)
    assert records[0]["capture"] == "scanning_app"
    assert records[0]["box"] == [10, 10, 400, 60]


def test_a_missing_optional_field_takes_its_default() -> None:
    text = json.dumps(
        {
            "page": "p1",
            "image": "p1.png",
            "page_size": [100, 100],
            "capture": "clean_scan",
            "box": [1, 1, 50, 20],
            "text": "x",
            "class": "print",
        }
    )
    line = page_from_jsonl(text).lines[0]
    assert line.status is TruthStatus.PREFILLED
    assert line.origin is TruthOrigin.TRANSCRIPTION


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(capture="fax"),
        lambda r: r.update(**{"class": "gothic"}),
        lambda r: r.update(box=[1, 2, 3]),
        lambda r: r.update(box=[50, 5, 10, 20]),
        lambda r: r.update(box=[0, 0, 5000, 20]),
        lambda r: r.update(text="two\nlines"),
        lambda r: r.update(status="verified", text="  "),
        lambda r: r.pop("text"),
        lambda r: r.update(page="Bad Id"),
        lambda r: r.update(image="../x.jpg"),
        lambda r: r.update(page_size=[0, 10]),
        lambda r: r.update(region_id="not-a-uuid"),
    ],
)
def test_bad_records_are_refused_without_echoing_text(mutate) -> None:  # type: ignore[no-untyped-def]
    record = json.loads(page_to_jsonl(page()).splitlines()[0])
    record["text"] = SECRET
    mutate(record)
    with pytest.raises(InvariantError) as error:
        page_from_jsonl(json.dumps(record, ensure_ascii=False))
    assert SECRET not in str(error.value)


def test_records_that_disagree_about_the_page_are_refused() -> None:
    lines = page_to_jsonl(page()).splitlines()
    second = json.loads(lines[1])
    second["capture"] = "phone_photo"
    with pytest.raises(InvariantError, match="capture differs"):
        page_from_jsonl("\n".join([lines[0], json.dumps(second)]))


def test_empty_and_non_json_files_are_refused() -> None:
    for text in ("", "\n\n", "not json", "[1, 2]"):
        with pytest.raises(InvariantError):
            page_from_jsonl(text)
    with pytest.raises(InvariantError):
        page(lines=())


# --- service ----------------------------------------------------------------------------------


def prefill(**changes: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "page_id": "b-ci1-p03",
        "capture": CaptureType.PHONE_PHOTO,
        "image": IMAGE,
        "image_name": "b-ci1-p03.jpg",
        "width": 1000,
        "height": 1400,
        "source": "B-CI1",
        "lines": [
            PrefillLine(box=box(), text="first  reading ", content_class=ContentClass.CURSIVE),
            PrefillLine(
                box=box(10, 100, 400, 150), text="second", content_class=ContentClass.PRINT
            ),
        ],
    }
    fields.update(changes)
    return fields


def test_prefill_stores_unverified_lines_with_the_reading_as_prefill() -> None:
    svc, store = service()
    stored = svc.add_prefilled(**prefill())  # type: ignore[arg-type]
    assert [ln.status for ln in stored.lines] == [TruthStatus.PREFILLED] * 2
    assert stored.lines[0].text == stored.lines[0].prefill == "first reading"
    assert stored.verified() == ()
    assert store.image("b-ci1-p03") == IMAGE


def test_prefill_does_not_replace_a_page_unless_asked_and_never_a_persons_work() -> None:
    svc, _ = service()
    svc.add_prefilled(**prefill())  # type: ignore[arg-type]
    with pytest.raises(InvariantError, match="already"):
        svc.add_prefilled(**prefill())  # type: ignore[arg-type]
    svc.add_prefilled(**prefill(), replace_prefill=True)  # type: ignore[arg-type]
    stored = svc.pages()[0]
    svc.save_lines(
        stored.id,
        [
            TruthLine(
                box=stored.lines[0].box,
                text="typed by a person",
                content_class=ContentClass.CURSIVE,
                status=TruthStatus.VERIFIED,
            ),
            stored.lines[1],
        ],
    )
    with pytest.raises(InvariantError, match="not overwritten"):
        svc.add_prefilled(**prefill(), replace_prefill=True)  # type: ignore[arg-type]
    assert svc.pages()[0].lines[0].text == "typed by a person"


def test_saving_lines_from_the_tool_validates_and_needs_a_known_page() -> None:
    svc, _ = service()
    svc.add_prefilled(**prefill())  # type: ignore[arg-type]
    with pytest.raises(NotFoundError):
        svc.save_lines("nope", [TruthLine(box=box(), text="x", content_class=ContentClass.PRINT)])
    with pytest.raises(InvariantError):  # outside the page
        svc.save_lines(
            "b-ci1-p03",
            [TruthLine(box=box(0, 0, 5000, 60), text="x", content_class=ContentClass.PRINT)],
        )


def test_a_correction_verifies_the_overlapping_line_and_keeps_its_prefill() -> None:
    svc, _ = service()
    svc.add_prefilled(**prefill())  # type: ignore[arg-type]
    region = uuid4()
    updated = svc.record_correction(
        page_id="b-ci1-p03",
        box=box(12, 12, 398, 58),  # IoU well above 0.5 with the first line
        text="the teacher's text",
        content_class=ContentClass.CURSIVE,
        region_id=region,
    )
    line = updated.lines[0]
    assert (line.text, line.status, line.origin) == (
        "the teacher's text",
        TruthStatus.VERIFIED,
        TruthOrigin.TEACHER_CORRECTION,
    )
    assert line.prefill == "first reading"
    assert line.region_id == region
    assert line.box == box()  # the page's own box is kept
    assert len(updated.lines) == 2


def test_a_correction_with_no_overlap_adds_a_line() -> None:
    svc, _ = service()
    svc.add_prefilled(**prefill())  # type: ignore[arg-type]
    updated = svc.record_correction(
        page_id="b-ci1-p03",
        box=box(10, 600, 400, 650),
        text="new line",
        content_class=ContentClass.PRINT,
    )
    assert len(updated.lines) == 3
    assert updated.lines[2].prefill is None


def test_a_correction_to_an_empty_text_ignores_the_line() -> None:
    svc, _ = service()
    svc.add_prefilled(**prefill())  # type: ignore[arg-type]
    updated = svc.record_correction(
        page_id="b-ci1-p03", box=box(), text="  ", content_class=ContentClass.CURSIVE
    )
    assert updated.lines[0].status is TruthStatus.IGNORED


def test_the_first_correction_of_a_new_page_creates_it_with_its_college_and_image() -> None:
    svc, store = service()
    college = uuid4()
    created = svc.record_correction(
        page_id="rev-1",
        box=box(),
        text="corrected",
        content_class=ContentClass.CURSIVE,
        new_page=NewPage(
            image=IMAGE,
            image_name="rev-1.jpg",
            capture=CaptureType.PHONE_PHOTO,
            width=1000,
            height=1400,
            college_id=college,
        ),
    )
    assert created.college_id == college
    assert [ln.text for ln in created.lines] == ["corrected"]
    assert store.image("rev-1") == IMAGE
    again = svc.record_correction(
        page_id="rev-1", box=box(10, 700, 300, 750), text="more", content_class=ContentClass.PRINT
    )
    assert len(again.lines) == 2
    with pytest.raises(NotFoundError):
        svc.record_correction(
            page_id="unknown", box=box(), text="x", content_class=ContentClass.PRINT
        )


def test_progress_counts_verified_lines_by_capture_and_class() -> None:
    svc, _ = service()
    svc.add_prefilled(**prefill())  # type: ignore[arg-type]
    svc.record_correction(
        page_id="b-ci1-p03", box=box(), text="a", content_class=ContentClass.CURSIVE
    )
    svc.record_correction(
        page_id="b-ci1-p03", box=box(10, 100, 400, 150), text="", content_class=ContentClass.PRINT
    )
    progress = svc.progress()
    assert (progress.pages, progress.lines, progress.verified, progress.ignored) == (1, 2, 1, 1)
    assert progress.prefilled == 0
    assert progress.by_capture == {CaptureType.PHONE_PHOTO: 1}
    assert progress.by_class == {ContentClass.CURSIVE: 1}
