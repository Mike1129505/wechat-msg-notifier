"""Parse the chat transcript area out of OCR lines + pixel colours."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .ocr import Line

DATE_RE = re.compile(r"(\d{4}年)?\d{1,2}月\d{1,2}日|^(昨天|前天|星期[一二三四五六日天])|^\d{1,2}:\d{2}$")
TIME_TAIL_RE = re.compile(r"(\d{1,2}:\d{2})$")
NUM_TITLE_RE = re.compile(r"\(\d+\)\s*$")

CONTENT_MAX_P10 = 110     # glyph brightness percentile: below this = normal black text


@dataclass
class Message:
    text: str
    direction: str  # "in" | "out"
    sender: str = ""
    y: int = 0
    stamp: str = ""
    lines: list[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        t = re.sub(r"\s+", "", self.text)
        return f"{self.direction}|{t}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"Message({self.direction}, {self.sender!r}, {self.stamp!r}, {self.text!r})"


@dataclass
class Chat:
    title: str
    is_group: bool
    messages: list[Message]


def _patch(img: np.ndarray, line: Line, pad: int = 0) -> np.ndarray:
    x0 = max(0, int(line.x) - pad)
    y0 = max(0, int(line.y) - pad)
    x1 = min(img.shape[1], int(line.x + line.w) + pad)
    y1 = min(img.shape[0], int(line.y + line.h) + pad)
    return img[y0:y1, x0:x1]


def glyph_brightness(img: np.ndarray, line: Line) -> float:
    """10th percentile of brightness inside the text box == the glyph colour."""
    patch = _patch(img, line)
    if patch.size == 0:
        return 255.0
    gray = patch.reshape(-1, 3).mean(axis=1)
    return float(np.percentile(gray, 10))


def bubble_color(img: np.ndarray, line: Line) -> tuple[int, int, int]:
    """Background colour just outside/above the glyph box == the bubble fill."""
    h, w, _ = img.shape
    ys = int(line.y + line.h / 2)
    pts = []
    for dx in (-7, -4, int(line.w) + 4, int(line.w) + 7):
        x = int(line.x + dx)
        if 0 <= x < w and 0 <= ys < h:
            pts.append(img[ys, x])
    y2 = int(line.y) - 6
    if 0 <= y2 < h:
        for dx in (2, 10, 20):
            x = int(line.x + dx)
            if 0 <= x < w:
                pts.append(img[y2, x])
    if not pts:
        return (255, 255, 255)
    arr = np.array(pts, dtype=int)
    return tuple(int(v) for v in np.median(arr, axis=0))


def is_green(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return g > 185 and g - r > 20 and g - b > 40


def parse_chat(lines: list[Line], img: Image.Image, area_left: int = 318,
               area_top: int = 34, title_bottom: int = 74,
               area_bottom: int = 500) -> Chat:
    arr = np.array(img.convert("RGB"))

    title_line = None
    body: list[Line] = []
    for l in lines:
        if l.x < area_left:
            continue
        if l.y < title_bottom:
            if title_line is None or l.y < title_line.y:
                title_line = l
        elif l.y <= area_bottom:
            body.append(l)

    title = title_line.text.strip() if title_line else ""
    is_group = bool(NUM_TITLE_RE.search(title))

    body.sort(key=lambda l: l.y)
    blocks: list[Message] = []
    cur: Message | None = None
    pending_sender = ""
    stamp = ""

    for l in body:
        text = l.text.strip()
        if not text:
            continue
        bright = glyph_brightness(arr, l)
        bg = bubble_color(arr, l)
        outgoing = is_green(bg)
        centered = 420 <= (l.x + l.w / 2) <= 650
        is_gray = bright >= CONTENT_MAX_P10

        if is_gray and centered and l.h <= 22:
            # date / time separator
            m = TIME_TAIL_RE.search(text.replace(" ", ""))
            stamp = m.group(1) if m else ""
            cur = None
            pending_sender = ""
            continue

        if is_gray and not outgoing and not centered:
            pending_sender = text
            cur = None
            continue

        if cur is not None and cur.direction == ("out" if outgoing else "in"):
            same_left = abs(l.x - cur.x0) <= 10          # type: ignore[attr-defined]
            same_right = abs((l.x + l.w) - cur.x2) <= 12  # type: ignore[attr-defined]
            if (same_left if not outgoing else same_right) and (l.y - cur.y_last) <= 12:  # type: ignore[attr-defined]
                cur.lines.append(text)
                cur.text = "\n".join(cur.lines)
                cur.y_last = l.y + l.h  # type: ignore[attr-defined]
                continue

        cur = Message(text=text, direction="out" if outgoing else "in",
                      sender="" if outgoing else pending_sender,
                      y=int(l.y), stamp=stamp, lines=[text])
        cur.x0 = l.x  # type: ignore[attr-defined]
        cur.x2 = l.x + l.w  # type: ignore[attr-defined]
        cur.y_last = l.y + l.h  # type: ignore[attr-defined]
        if not outgoing:
            pending_sender = ""
        blocks.append(cur)

    if not is_group:
        for m in blocks:
            if m.direction == "in":
                m.sender = title
    return Chat(title=title, is_group=is_group, messages=blocks)
