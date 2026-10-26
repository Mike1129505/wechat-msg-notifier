"""Parse the WeChat session list (left panel) out of OCR lines."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ocr import Line

DRAFT_RE = re.compile(r"^[\[【]草稿[\]】]?")
HEADER_RE = re.compile(r"^[【\[]\s*.{1,24}?\s*[】\]]\s*(\d{1,2}:\d{2}|\d{4}/\d{1,2}/\d{1,2})")

# The avatar column ends around x=110 (logical px); chat text starts at ~124. Anything
# OCR finds to the left of this is a badge ("10", "12)") or a profile picture, not a name.
TEXT_LEFT_L = 112


def header_like(name: str) -> bool:
    """Our own relay headers look like 【张三】12:30 …; a real chat name rarely does."""
    n = name.strip()
    return bool(n) and n[0] in "【[" and ("】" in n or "]" in n)
# "12:40", "昨天 18:38", "2025/01/25" — OCR often glues the row's timestamp onto the
# end of a long chat name because the two are visually close.
DATE_SUFFIX_RE = re.compile(
    r"(?:昨天|今天|前天|星期[一二三四五六日天])?\s*"
    r"(\d{4}/\d{1,2}/\d{1,2}|\d{1,2}:\d{2})\s*$")
TIME_RE = re.compile(
    r"^(?=.*\d)[\d/:年月日上下午昨天前天星期一二三四五六日\s]+$")


def clean_name(name: str) -> str:
    """Drop OCR leftovers glued to a chat name: company tags, trailing dots."""
    # WeChat renders a company tag after the name, e.g. "炉石心… @网易…"
    tag = re.search(r"(?<=.)@[^\s@]{1,20}$", name)
    if tag:
        name = name[:tag.start()]
    name = re.sub(r"(?:[.．。…·~～\s]+)$", "", name)
    return name.strip()


@dataclass
class Row:
    name: str
    preview: str = ""
    time: str = ""
    y: int = 0
    name_y: int = 0
    height: int = 0
    raw: list[str] = field(default_factory=list)
    uncertain: bool = False
    reason: str = ""

    @property
    def key(self) -> str:
        return normalize(self.name)

    def signature(self) -> str:
        return f"{normalize(self.preview)}|{self.time}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"Row({self.name!r}, {self.preview!r}, {self.time!r}, y={self.y})"


def normalize(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _is_timeish(line: Line, scale: float = 1.0) -> bool:
    t = normalize(line.text)
    if not t:
        return False
    if line.x < 215 * scale:
        # merged name+time occasionally, e.g. "12022/06/18"
        return False
    return bool(TIME_RE.match(t)) and len(t) <= 14


def parse_rows(lines: list[Line], list_right: int = 312, top: int = 70,
               list_bottom: int = 600, scale: float = 1.0) -> list[Row]:
    """Cluster OCR lines in the left panel into session rows.

    All geometry below was measured at 100% display scaling; ``scale`` converts it
    to the live window's physical pixels (a 150% screen has 33px name->preview gaps,
    which used to split every row into two and produce phantom "unknown" chats).
    """
    panel = [l for l in lines
             if TEXT_LEFT_L * scale <= l.x < list_right and top < l.y < list_bottom]
    panel.sort(key=lambda l: l.y)

    clusters: list[list[Line]] = []
    for line in panel:
        if not clusters:
            clusters.append([line])
            continue
        prev = clusters[-1][-1]
        if line.y - prev.y > 30 * scale:
            clusters.append([line])
        else:
            clusters[-1].append(line)

    rows: list[Row] = []
    for cl in clusters:
        texts = [normalize(l.text) for l in cl]
        name_line = None
        time_line = None
        for l in cl:
            if _is_timeish(l, scale):
                if time_line is None:
                    time_line = l
                continue
            if name_line is None:
                name_line = l
            elif l.y > name_line.y + 6 * scale and name_line:
                pass
        if name_line is None:
            # A row whose name/preview OCR failed but whose timestamp survived: keep it
            # as an uncertain row so the name-retry pass can still recover it.
            if time_line is not None:
                rows.append(Row(name="", preview="", time=normalize(time_line.text),
                                y=int(time_line.y), name_y=int(time_line.y),
                                height=int(time_line.h), raw=texts,
                                uncertain=True, reason="noname"))
            continue
        # the first non-time line is the chat name; the next one (below) is the preview
        rest = [l for l in cl if l is not name_line and not _is_timeish(l, scale)
                and l.y > name_line.y + 6 * scale]
        preview = normalize(rest[0].text) if rest else ""
        raw_name = normalize(name_line.text)
        suffix = DATE_SUFFIX_RE.search(raw_name)
        inline_time = suffix.group(0).strip() if suffix else ""
        name = clean_name(DATE_SUFFIX_RE.sub("", raw_name))
        uncertain = False

        name_y = int(name_line.y)
        if not rest:
            # Only one text line in this row. Either the preview failed to OCR (the name
            # is still fine) or the *name* failed and we are looking at the preview.
            looks_like_preview = len(name) > 10 or name_line.h < 15 * scale
            if looks_like_preview:
                # treat the text as the preview so the row can still be reported
                preview, name, uncertain = name, "", True
                name_y = int(name_line.y - name_line.h * 1.2)
        reason = "unknown" if uncertain else ""
        if DRAFT_RE.match(name):
            # a chat with an unsent draft shows "[草稿] ..." instead of its name
            uncertain, reason = True, "draft"
        if header_like(name):
            # Our own forwarded messages look like "【张三】12:12 + 内容" and land in the
            # target chat's preview. If OCR splits that preview it can pose as a chat name.
            uncertain, reason = True, "header"
        plain = normalize(name)
        if plain and (plain.isdigit() or TIME_RE.match(plain)):
            # A timestamp from the right-hand column got mistaken for the chat name.
            name, uncertain, reason = "", True, "time"
        rows.append(Row(name=name, preview=preview,
                        time=normalize(time_line.text) if time_line else inline_time,
                        y=int(name_line.y), name_y=name_y,
                        height=int(name_line.h), raw=texts,
                        uncertain=uncertain, reason=reason))
    return [r for r in rows if r.name or r.preview or r.uncertain]


def find_row(rows: list[Row], name: str) -> Row | None:
    target = normalize(name)
    for r in rows:
        if r.key == target:
            return r
    return None
