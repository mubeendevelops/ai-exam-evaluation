"""Generated pages and "photographs" of them, for tests (never real student pages). Tests only.

``ruled_page`` draws a school-notebook page: ruled lines, a red margin, and lines of script-like
text in blue ink. ``photograph`` puts a page on a table at an angle, with a shadow, or fills
the frame like a scan; ``spread`` adds the neighbouring page of an open notebook."""

import cv2
import numpy as np

from tarn_adapters.imaging.locate import Image

WORDS = (
    "the", "court", "may", "hear", "appeals", "from", "lower", "courts", "and", "decide",
    "questions", "of", "law", "in", "public", "interest", "under", "article", "supreme",
    "rights", "state", "union", "federal", "judicial", "review", "powers", "duty",
)  # fmt: skip


def ruled_page(
    width: int = 1240, height: int = 1754, *, seed: int = 1, ruled: bool = True, lines: int = 30
) -> Image:
    rng = np.random.default_rng(seed)
    page = np.full((height, width, 3), 244, np.uint8)
    top, step = 150, (height - 220) // lines
    if ruled:
        for y in range(top, height - 40, step):
            cv2.line(page, (0, y), (width, y), (190, 160, 130), 2)
        cv2.line(page, (int(width * 0.11), 0), (int(width * 0.11), height), (90, 90, 225), 3)
    left = int(width * 0.14)
    for n in range(lines - 1):
        y = top + n * step + step - 8
        x = left + int(rng.integers(0, 3)) * 30
        while x < width - 160:
            word = str(rng.choice(WORDS))
            cv2.putText(
                page,
                word,
                (x, y),
                cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
                1.5,
                (130, 50, 20),
                3,
                cv2.LINE_AA,
            )
            x += int(len(word) * 34 + 30)
            if rng.random() < 0.05:
                break
    return page


def _table(width: int, height: int, seed: int, colour: tuple[int, int, int]) -> Image:
    rng = np.random.default_rng(seed)
    raw = rng.normal(0, 1, (height // 4, width // 4)).astype(np.float32)
    noise: Image = cv2.resize(cv2.GaussianBlur(raw, (0, 0), 3), (width, height))
    grain = (noise * 14)[:, :, None]
    table: Image = np.clip(np.array(colour, np.float32)[None, None, :] + grain, 0, 255)
    return table.astype(np.uint8)


def photograph(
    page: Image,
    *,
    size: tuple[int, int] = (1500, 2000),
    margin: float = 0.07,
    angle: float = 0.0,
    tilt: float = 0.0,
    table: tuple[int, int, int] = (25, 45, 80),
    shadow: bool = False,
    seed: int = 3,
) -> Image:
    """The page on a table. ``angle`` turns it (degrees), ``tilt`` makes one side nearer
    (perspective, as a fraction of the width)."""
    width, height = size
    canvas = _table(width, height, seed, table)
    ph, pw = page.shape[:2]
    mx, my = width * margin, height * margin
    quad = np.array(
        [[mx, my], [width - mx, my], [width - mx, height - my], [mx, height - my]], np.float32
    )
    quad[1, 1] += tilt * width
    quad[2, 1] -= tilt * width
    centre = quad.mean(axis=0)
    rotation = cv2.getRotationMatrix2D((float(centre[0]), float(centre[1])), angle, 1.0)
    quad = cv2.transform(quad[None], rotation)[0].astype(np.float32)
    source = np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], np.float32)
    matrix = cv2.getPerspectiveTransform(source, quad)
    paper = cv2.warpPerspective(page, matrix, size, flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.full((ph, pw), 255, np.uint8), matrix, size)
    out = np.where(mask[:, :, None] > 127, paper, canvas)
    if shadow:
        ramp = np.linspace(1.0, 0.55, width, dtype=np.float32)[None, :, None]
        out = np.where(mask[:, :, None] > 127, out * ramp, out)
    return np.asarray(out, dtype=np.uint8)


def scan(page: Image, *, size: tuple[int, int] = (1240, 1754)) -> Image:
    """A page that fills the frame (a flat scan or a close, square-on photo)."""
    return cv2.resize(page, size, interpolation=cv2.INTER_AREA)


def spread(
    page: Image, *, strip: float = 0.14, seed: int = 5, size: tuple[int, int] = (1500, 2000)
) -> Image:
    """An open notebook: the main page with a narrow sliver of the next page to its right,
    parted by a dark crease, in a frame that the pages fill."""
    width, height = size
    main_w = int(width * (1 - strip))
    main = cv2.resize(page, (main_w - 12, height))
    other = cv2.resize(ruled_page(seed=seed), (width - main_w - 4, height))
    crease = np.full((height, 16, 3), 60, np.uint8)
    for x in range(16):  # a soft shadow into the crease
        crease[:, x] = int(60 + abs(x - 8) * 14)
    return np.hstack([main, crease, other])[:, :width]


def jpeg(image: Image, quality: int = 92) -> bytes:
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return encoded.tobytes()


def png(image: Image) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("PNG encoding failed")
    return encoded.tobytes()
