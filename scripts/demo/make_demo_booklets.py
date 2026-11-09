"""Generate synthetic handwritten-looking answer booklets (PDF) for the admin demo.

Reads the demo college's roster, units and reference answers from the dev
database and writes one PDF per student, named by roll number, into
var/demo/booklets/<unit code>/. Drop them on /admin/upload/units/<id>.
Everything is synthetic: fictitious students, text from the seed's keys.
Development only. Run: python3 scripts/demo/make_demo_booklets.py
"""

from __future__ import annotations

import json
import random
import re
import subprocess
import textwrap
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var" / "demo" / "booklets"
FONTS = ROOT / "var" / "demo" / "fonts"
W, H = 1240, 1754  # A4 at 150 dpi
LINE = 52
TOP = 230
LEFT = 150

# unit code -> (paper exam code, question codes to answer, label for each)
PLANS = {
    "U-0003": ("QP-CI-2021-06", ["QP-CI-Q1", "QP-CI-Q2", "QP-CI-Q3", "QP-CI-Q4", "QP-CI-Q5",
                                 "QP-CI-Q8", "QP-CI-Q9", "QP-CI-Q10", "QP-CI-Q11",
                                 "QP-CI-Q15", "QP-CI-Q16"]),
    "U-0004": ("QP-IPR-2021-07", ["QP-IPR-Q1", "QP-IPR-Q2", "QP-IPR-Q3", "QP-IPR-Q4", "QP-IPR-Q5",
                                  "QP-IPR-Q8", "QP-IPR-Q9", "QP-IPR-Q10"]),
    "U-0005": ("IEL-A1", ["A1-Q1", "A1-Q2"]),
}
# quality of each student's answers: share of the key's sentences written
PROFILES = [1.0, 0.85, 0.7, 0.55, 0.4, 0.8]
STUDENTS_PER_UNIT = 6


def sql(query: str) -> list[list[str]]:
    """Run a query; rows come back as JSON arrays so multi-line text survives."""
    wrapped = f"select coalesce(json_agg(row_to_json(t)), '[]') from ({query}) t"
    out = subprocess.run(
        ["docker", "exec", "tarn-postgres-1", "psql", "-U", "tarn", "-d", "tarn", "-At", "-c", wrapped],
        check=True, capture_output=True, text=True,
    ).stdout
    return [list(row.values()) for row in json.loads(out)]


def label_of(code: str) -> str:
    return re.sub(r"^.*-Q", "", code)


def paper(w: int = W, h: int = H) -> Image.Image:
    img = Image.new("RGB", (w, h), (250, 248, 240))
    d = ImageDraw.Draw(img)
    for y in range(TOP, h - 80, LINE):
        d.line([(60, y), (w - 60, y)], fill=(176, 196, 222), width=2)
    d.line([(LEFT - 20, 0), (LEFT - 20, h)], fill=(214, 120, 120), width=3)
    d.line([(w - 70, 0), (w - 70, h)], fill=(214, 120, 120), width=2)
    return img


def write_text(img: Image.Image, font_path: Path, x: int, y: int, text: str, rng: random.Random, size: int) -> None:
    """Write one line word by word with wobble in size, baseline and tilt."""
    d = ImageDraw.Draw(img)
    cx = x
    for word in text.split(" "):
        fs = size + rng.randint(-3, 3)
        font = ImageFont.truetype(str(font_path), fs)
        tile = Image.new("RGBA", (int(font.getlength(word)) + 30, fs * 2), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((10, 6), word, font=font, fill=(24, 36, 110, 235))
        tile = tile.rotate(rng.uniform(-2.5, 2.5), resample=Image.BICUBIC, expand=True)
        img.paste(tile, (int(cx) - 10, y - fs + rng.randint(-3, 3)), tile)
        cx += font.getlength(word + " ") + rng.randint(0, 5)
    del d


def pick_sentences(answer: str, share: float, rng: random.Random) -> str:
    parts = re.split(r"(?<=[.;])\s+", answer.strip())
    keep = max(1, round(len(parts) * share))
    idx = sorted(rng.sample(range(len(parts)), keep))
    return " ".join(parts[i] for i in idx)


def render(student: dict, items: list[tuple[str, str]], font_path: Path, rng: random.Random, blurry: bool) -> list[Image.Image]:
    pages: list[Image.Image] = []
    img = paper()
    f = ImageFont.truetype(str(font_path), 38)
    ImageDraw.Draw(img).text((LEFT, 90), f"Name: {student['name']}", font=f, fill=(24, 36, 110))
    ImageDraw.Draw(img).text((LEFT, 140), f"USN: {student['usn']}", font=f, fill=(24, 36, 110))
    y = TOP + LINE
    per_line = 62
    for label, answer in items:
        lines = textwrap.wrap(f"{label}. {answer}", per_line)
        if y + LINE * min(len(lines), 2) > H - 120:
            pages.append(img)
            img, y = paper(), TOP + LINE
        for ln in lines:
            if y > H - 120:
                pages.append(img)
                img, y = paper(), TOP + LINE
            write_text(img, font_path, LEFT + rng.randint(0, 6), y, ln, rng, 34)
            y += LINE
        y += LINE // 2
    pages.append(img)
    out = []
    for p in pages:
        p = p.rotate(rng.uniform(-0.8, 0.8), resample=Image.BICUBIC, fillcolor=(250, 248, 240))
        if blurry:
            p = p.filter(ImageFilter.GaussianBlur(7)).point(lambda v: int(v * 0.55))
        else:
            p = p.filter(ImageFilter.GaussianBlur(0.6))
        out.append(p)
    return out


def save_pdf(pages: list[Image.Image], path: Path) -> None:
    """One A4 page per image (Pillow here has no JPEG encoder, so reportlab)."""
    c = canvas.Canvas(str(path), pagesize=A4)
    for p in pages:
        c.drawImage(ImageReader(p), 0, 0, width=A4[0], height=A4[1])
        c.showPage()
    c.save()


def main() -> None:
    cid = sql("select id from colleges where code='DEMO_COM'")[0][0]
    students = [
        {"usn": r[0], "name": r[1]}
        for r in sql(f"select usn,name from students where college_id='{cid}' and usn like 'DEMOC%' order by usn")
    ]
    for unit, (_exam, codes) in PLANS.items():
        answers = {
            r[0]: r[1]
            for r in sql(
                "select qv.code, ra.text from reference_answers ra "
                "join question_versions qv on qv.question_id=ra.question_id and qv.college_id=ra.college_id "
                f"where ra.college_id='{cid}' and not ra.retired"
            )
        }
        folder = OUT / unit
        folder.mkdir(parents=True, exist_ok=True)
        for n, st in enumerate(students[:STUDENTS_PER_UNIT]):
            rng = random.Random(f"{unit}{st['usn']}")
            font = FONTS / ("Caveat.ttf" if n == 3 else "Kalam-Regular.ttf")
            share = PROFILES[n % len(PROFILES)]
            items = [(label_of(c), pick_sentences(answers[c], share, rng)) for c in codes if c in answers]
            if n == 4:  # leaves two answers unattempted-looking short
                items = [(lb, a if i % 3 else a.split(".")[0] + ".") for i, (lb, a) in enumerate(items)]
            pages = render(st, items, font, rng, blurry=False)
            save_pdf(pages, folder / f"{st['usn']}.pdf")
        # a blurred, dark photo: should trip the quality gate
        rng = random.Random(unit + "blur")
        st = students[STUDENTS_PER_UNIT]
        items = [(label_of(c), pick_sentences(answers[c], 0.7, rng)) for c in codes[:3] if c in answers]
        pages = render(st, items, FONTS / "Kalam-Regular.ttf", rng, blurry=True)
        save_pdf(pages, folder / f"{st['usn']}.pdf")
        # a roll number that is not on the roster: refused with a reason
        pages = render({"name": "Unknown Person", "usn": "DEMOX9999"}, items[:2], FONTS / "Kalam-Regular.ttf", rng, False)
        save_pdf(pages, folder / "DEMOX9999.pdf")
        print(unit, len(list(folder.glob("*.pdf"))), "files in", folder)


if __name__ == "__main__":
    main()
