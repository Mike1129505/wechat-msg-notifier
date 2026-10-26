"""Read-only session-list watcher: screenshot + OCR, never touches the mouse."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from . import winapi
from .ocr import Ocr
from .sessionlist import Row, parse_rows, TEXT_LEFT_L, TIME_RE, clean_name

# All offsets were measured on a 896x648 window at 100% display scaling. They are
# expressed in "logical pixels" and multiplied by the window's DPI scale at runtime,
# so the tool keeps working on a 125%/150% laptop screen or a resized window.
BASE_W, BASE_H = 896, 648
LIST_RIGHT_L = 314
LIST_TOP_L = 70
TITLE_BOX_L = (318, 30, 700, 80)
BAND_X_L = 300
UNREAD_X_L = (104, 128)
UNREAD_PAD_L = (13, 11)
DIFF_X_L = (60, 300)

log = logging.getLogger("wfbot")

# Text that only shows up when WeChat is on its login / loading / modal screen.
# Rows made of these words (and nothing else) are UI chrome, not conversations.
UI_NOISE = {"登录", "取消", "确定", "正在进入", "收取中", "扫码登录", "切换账号",
            "进入微信", "退出登录", "进入", "加载中"}


class Layout:
    """Pixel offsets derived from the live window size + DPI scale."""

    def __init__(self, w: int, h: int, scale: float) -> None:
        self.w, self.h, self.scale = w, h, scale
        self.list_right = min(w, round(LIST_RIGHT_L * scale))
        self.list_top = round(LIST_TOP_L * scale)
        self.list_box = (0, self.list_top, self.list_right, h)
        self.title_box = tuple(round(v * scale) for v in TITLE_BOX_L)
        self.band_x = min(round(BAND_X_L * scale), w - 2)
        self.unread_x = tuple(round(v * scale) for v in UNREAD_X_L)
        self.unread_pad = tuple(round(v * scale) for v in UNREAD_PAD_L)
        self.diff_x = (round(DIFF_X_L[0] * scale), round(DIFF_X_L[1] * scale))
        self.row_top = round(8 * scale)
        self.row_bottom = round(46 * scale)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Layout({self.w}x{self.h}, scale={self.scale:.2f}, list<{self.list_right})"


def dpi_scale(hwnd: int) -> float:
    try:
        dpi = winapi.user32.GetDpiForWindow(int(hwnd))
        return max(1.0, dpi / 96.0) if dpi else 1.0
    except Exception:  # pragma: no cover
        return 1.0


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _green_band(arr, x: int | None = None) -> tuple[int, int] | None:
    """Find the y-range of the highlighted (green) session row."""
    h, w, _ = arr.shape
    x = min(x if x is not None else 300, w - 1)
    runs: list[tuple[int, int]] = []
    start = None
    for y in range(60, h):
        px = arr[y, x]
        r, g, b = int(px[0]), int(px[1]), int(px[2])
        green = g > 150 and g - r > 25 and g - b > 35
        if green and start is None:
            start = y
        elif not green and start is not None:
            if y - start >= 24:
                runs.append((start, y))
            start = None
    if start is not None and h - start >= 24:
        runs.append((start, h))
    return max(runs, key=lambda t: t[1] - t[0]) if runs else None


def _band_diff(a, b, row: Row, layout: "Layout") -> float:
    """Mean pixel difference of one session row between two frames."""
    base = row.name_y or row.y
    y0 = max(0, int(base) - layout.row_top)
    y1 = min(a.shape[0], int(base) + layout.row_bottom)
    pa, pb = a[y0:y1, layout.diff_x[0]:layout.diff_x[1]], b[y0:y1, layout.diff_x[0]:layout.diff_x[1]]
    if pa.shape != pb.shape or pa.size == 0:
        return 999.0
    return float(np.abs(pa - pb).mean())


PUNCT_MAP = str.maketrans({"？": "?", "！": "!", "，": ",", "。": ".", "：": ":",
                          "；": ";", "（": "[", "）": "]", "【": "[", "】": "]",
                          "「": "[", "」": "]", "〔": "[", "〕": "]",
                          "…": ".", "～": "~", "　": "",
                          "“": '"', "”": '"', "‘": "'", "’": "'"})


def _canon(text: str) -> str:
    """Fold punctuation/ellipsis differences that OCR keeps flip-flopping on."""
    t = norm(text).translate(PUNCT_MAP)
    t = re.sub(r"\.{2,}", ".", t)
    return t.strip(".")


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _identity(name: str) -> str:
    """OCR drops/duplicates punctuation in long names; compare the letters only."""
    return re.sub(r"[^\w]+", "", norm(name), flags=re.UNICODE)


def _nearest_key(prev: dict[str, str], key: str) -> str | None:
    """Find the previous key that is really the same chat read slightly differently."""
    ident = _identity(key)
    if not ident:
        return None
    for k in prev:
        if not k.startswith("anon:") and _identity(k) == ident:
            return k
    from difflib import SequenceMatcher
    best, score = None, 0.0
    for k in prev:
        if k.startswith("anon:"):
            continue
        s = SequenceMatcher(None, _identity(k), ident).ratio()
        if s > score:
            best, score = k, s
    return best if score >= 0.90 else None


def _today_hhmm(time_text: str) -> datetime | None:
    """Parse a HH:MM row timestamp into today's datetime (None for dates/'昨天')."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (time_text or "").strip())
    if not m:
        return None
    now = datetime.now()
    try:
        ts = now.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                         second=0, microsecond=0)
    except ValueError:
        return None
    if ts > now + timedelta(minutes=5):    # 23:59 seen just after midnight
        ts -= timedelta(days=1)
    return ts


def _minutes_ago(time_text: str) -> float | None:
    ts = _today_hhmm(time_text)
    return None if ts is None else (datetime.now() - ts).total_seconds() / 60.0


def _anon_key(text: str) -> str:
    """Stable identity for a row whose chat name could not be read."""
    import hashlib
    return hashlib.blake2b(_canon(text).encode("utf-8"), digest_size=6).hexdigest()


def _in_band(row: Row, band: tuple[int, int]) -> bool:
    yc = row.y + max(row.height, 20) / 2
    return band[0] <= yc <= band[1]


def _has_unread(arr, row: Row, layout: "Layout") -> bool:
    """WeChat paints an unread badge at the avatar's top-right corner."""
    base = row.name_y or row.y
    y0 = max(0, int(base) - layout.unread_pad[0])
    y1 = min(arr.shape[0], int(base) + layout.unread_pad[1])
    sub = arr[y0:y1, layout.unread_x[0]:layout.unread_x[1]]
    if sub.size == 0:
        return False
    r, g, b = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    red = (r > 175) & (g < 125) & (b < 125) & (np.abs(g - b) < 60)
    return int(red.sum()) >= 30


class NotVisible(RuntimeError):
    pass


@dataclass
class Change:
    name: str
    preview: str
    time: str


class SessionWatcher:
    """Watches the WeChat session list and reports chats whose preview changed."""

    def __init__(self, ocr: Ocr, exclude: tuple[str, ...] = (),
                 only_from: tuple[str, ...] = (), require_unread: bool = True,
                 push_unrecognized: bool = False) -> None:
        self.ocr = ocr
        self.exclude = {norm(x) for x in exclude}
        self.only = {norm(x) for x in only_from}
        self.require_unread = require_unread
        # A row whose name OCR cannot read is keyed by its preview and bypasses the
        # exclude list, so e.g. an official-account row that OCR splits would leak
        # through. Off by default: silent is better than pushing the wrong chat.
        self.push_unrecognized = push_unrecognized
        self.prev: dict[str, str] | None = None
        self.recent: dict[str, list[str]] = {}
        self.cand: dict[str, tuple[str, int, float]] = {}
        self._warned: set[str] = set()
        self.settle: float = 15.0
        self._frame_hash: bytes | None = None
        self.last_rows: int = 0
        self.last_usable: int = 0
        self.last_overlap: int = 0
        self.last_noise: int = 0
        self.last_burst: int = 0
        self.known: set[str] = set()
        self.last_parse_ts: float = 0.0
        self.prev_parse_ts: float = 0.0
        self.layout = Layout(BASE_W, BASE_H, 1.0)

    def _capture(self):
        try:
            main = winapi.main_window()
        except Exception as exc:  # noqa: BLE001
            raise NotVisible("找不到微信主窗口") from exc
        if winapi.is_minimized(main.hwnd):
            raise NotVisible("微信主窗口被最小化了，读不到画面")
        img = winapi.capture(main.hwnd)
        self.layout = Layout(img.width, img.height, dpi_scale(main.hwnd))
        return img

    def _parse(self, img) -> list[Row]:
        self.prev_parse_ts = self.last_parse_ts
        box = self.layout.list_box
        crop = img.crop(box)
        lines = self.ocr.lines(crop)
        ox, oy = box[0], box[1]
        for l in lines:
            l.x += ox
            l.y += oy
        rows = parse_rows(lines, list_right=self.layout.list_right, top=0,
                          list_bottom=img.height, scale=self.layout.scale)
        self.last_rows = len([r for r in rows if not r.uncertain])
        self.last_noise = sum(
            1 for r in rows
            if norm(r.name) in UI_NOISE and not (r.preview or r.time))

        # The highlighted row is white-on-green and OCR misreads its name. Reading the
        # chat title costs a second OCR pass, so only do it when that row looks broken.
        import numpy as np
        band = _green_band(np.array(img.convert("RGB")), self.layout.band_x)
        if band:
            def in_band(r: Row) -> bool:
                yc = r.y + max(r.height, 20) / 2
                return band[0] <= yc <= band[1]

            broken = any(in_band(r) and r.uncertain for r in rows) or not any(in_band(r) for r in rows)
            if broken:
                title = self._title(img)
                if len(norm(title)) >= 2:
                    for r in rows:
                        if in_band(r):
                            r.name, r.uncertain = title, False
        # Empty previews are usually one-character messages, not media. Recover them.
        # Rows whose name the first pass dropped get a second, localised OCR attempt.
        for r in rows:
            if r.uncertain and not r.name:
                recovered = self._retry_name(img, r)
                if recovered:
                    # If the "recovered" text is just a fragment of what was parsed as
                    # the preview, the real name is the longer line: promote it.
                    ri, pi = _identity(recovered), _identity(r.preview)
                    if r.preview and ri and pi.startswith(ri) and len(pi) > len(ri):
                        r.name = clean_name(r.preview)
                        r.preview = ""
                    else:
                        r.name = recovered
                    r.uncertain, r.reason = False, ""
            if not r.preview and not r.uncertain:
                recovered = self._retry_preview(img, r)
                if recovered:
                    r.preview = recovered
        self.last_rows = len([r for r in rows if not r.uncertain])
        self.last_parse_ts = time.time()
        return rows

    def _title(self, img) -> str:
        lines = self.ocr.lines(img.crop(self.layout.title_box))
        if not lines:
            return ""
        best = min(lines, key=lambda l: l.x)
        t = re.sub(r"[\(（]\d+[\)）].*$", "", best.text).strip()
        return t.rstrip("~～·.。 ").strip()

    def _retry_name(self, img, row: Row) -> str:
        """Recover a name the first OCR pass dropped (e.g. a single-character name).

        Same idea as _retry_preview: crop only the name line, upscale it and use a
        lower score cut-off, then keep the topmost text line.
        """
        if self.ocr is None:
            return ""
        scale = self.layout.scale
        x0 = int(TEXT_LEFT_L * scale)
        x1 = min(int(240 * scale), self.layout.list_right)   # never the time column
        # For a name-less row, row.y is the preview line; take a 30px band above it so
        # the crop can never read the preview back, and so the whole strip is handed to
        # the recogniser without detection (single characters get dropped otherwise).
        # A row with no preview at all keeps its name on the timestamp line itself.
        # Try a few vertical offsets: the name baseline floats by a couple of pixels.
        if row.preview:
            base = int(row.y - 6 * scale)
            starts = [base - int(k * scale) - 30 for k in (0, 3, 6)]
        else:
            base = int(row.y - 6 * scale)
            starts = [base + int(k * scale) for k in (0, 3, 6)]

        best, best_score = "", 0.0
        for y0 in starts:
            y0 = max(0, y0)
            y1 = min(img.height, y0 + 30)
            if y1 - y0 < 8:
                continue
            try:
                lines = self.ocr.lines(img.crop((x0, y0, x1, y1)), text_score=0.3)
            except Exception:  # noqa: BLE001
                continue
            for l in lines:
                if l.score < 0.33:
                    continue
                cand_name = clean_name(l.text)
                plain = norm(cand_name)
                if not cand_name or TIME_RE.match(plain) or plain.isdigit():
                    continue
                if not (1 <= len(cand_name) <= 20) or not re.search(r"[\w\u4e00-\u9fff]", cand_name):
                    continue
                if norm(cand_name) == norm(row.preview):
                    continue
                if l.score > best_score:
                    best, best_score = cand_name, l.score
        if best:
            log.debug("名字重试：%r (%.2f)", best, best_score)
        return best

    def _retry_preview(self, img, row: Row) -> str:
        """Recover a one-character preview that the score cut-off threw away.

        RapidOCR reads "留" at ~0.499 confidence, just under the default 0.50, so the
        row looks like a non-text message. Re-OCR just that line as a single box
        (<=30px tall skips detection) with a lower cut-off.
        """
        if self.ocr is None:
            return ""
        scale = self.layout.scale
        y0 = int(row.name_y + row.height + 2 * scale)
        y1 = min(y0 + 30, img.height)
        if y1 - y0 < 8:
            return ""
        strip = img.crop((int(TEXT_LEFT_L * scale), y0, self.layout.list_right, y1))
        try:
            lines = self.ocr.lines(strip, text_score=0.3)
        except Exception:  # noqa: BLE001
            return ""
        text = "".join(l.text for l in lines if l.score >= 0.40).strip()
        plain = norm(text)
        if plain and (plain.isdigit() or TIME_RE.match(plain)):
            return ""
        if 1 <= len(text) <= 12 and re.search(r"[\w\u4e00-\u9fff]", text):
            log.debug("空预览重试：%r -> %r", row.name, text)
            return text
        return ""

    def _usable(self, rows, arr) -> tuple[dict[str, Row], dict[str, str]]:
        """Readable names are the identity; unreadable ones fall back to their preview."""
        usable: dict[str, Row] = {}
        labels: dict[str, str] = {}
        for r in rows:
            if not r.uncertain:
                key = norm(r.name)
                usable[key] = r
                labels[key] = r.name.strip()
            elif _has_unread(arr, r, self.layout):
                if not self.push_unrecognized:
                    log.debug("跳过未识别会话：名字认不出，不推送（预览 %r）", r.preview)
                    continue
                key = "anon:" + _anon_key(r.preview)
                usable[key] = r
                labels[key] = "未识别会话"
        return usable, labels

    def _learn(self, usable: dict[str, Row]) -> None:
        """Remember names that look like real chats so a login screen can be told apart.

        Only rows with a preview or a timestamp are learned: UI chrome such as "登录"
        or "取消" never has either, so it cannot poison the known-name set.
        """
        names = {k: r for k, r in usable.items() if not k.startswith("anon:")}
        self.last_usable = len(names)
        self.last_overlap = sum(1 for k in names if k in self.known)
        self.known |= {k for k, r in names.items() if r.preview or r.time}

    def _trust_new_row(self, rank: int, time_text: str) -> bool:
        """Can a never-seen row at this position be a brand-new message?

        A pinned chat can hold row 0 forever, so a genuinely new chat may land on
        row 1. Its HH:MM must be recent, or newer than the last frame we managed to
        read — the latter covers a closed lid / screen off for hours, where messages
        only become visible (with an already-old timestamp) when the screen returns.
        """
        if rank <= 0:
            return True
        if rank > 2:
            return False
        ts = _today_hhmm(time_text)
        if ts is None:
            return False
        if (datetime.now() - ts).total_seconds() <= 30 * 60:
            return True
        if self.prev_parse_ts <= 0:
            return False
        return ts >= datetime.fromtimestamp(self.prev_parse_ts) - timedelta(minutes=10)

    def baseline(self) -> list[Row]:
        img = self._capture()
        rows = self._parse(img)
        arr = np.asarray(img.convert("RGB"), dtype=np.int16)
        usable, labels = self._usable(rows, arr)
        self._learn(usable)
        self.last_overlap = self.last_usable   # startup is never "abnormal"
        self.prev = {k: r.signature() for k, r in usable.items()}
        self.labels = labels
        return rows

    def _hash(self, img) -> bytes:
        import hashlib
        crop = img.crop(self.layout.list_box).convert("L")
        return hashlib.blake2b(np.asarray(crop).tobytes(), digest_size=16).digest()

    def poll(self) -> list[Change]:
        img1 = self._capture()
        digest = self._hash(img1)
        if digest == self._frame_hash:
            return []          # nothing on screen changed: no OCR needed
        self._frame_hash = digest
        arr = np.asarray(img1.convert("RGB"), dtype=np.int16)
        rows = self._parse(img1)
        usable, labels = self._usable(rows, arr)
        self._learn(usable)

        if self.prev is None:
            self.prev = {k: r.signature() for k, r in usable.items()}
            self.labels = labels
            return []
        self.labels = labels

        order = {k: i for i, k in enumerate(
            sorted(usable, key=lambda k: usable[k].y))}

        # Two independent checks decide whether something is really a new message:
        #   1. WeChat itself must be showing an unread badge on that row
        #   2. the preview text must have changed *meaningfully* (OCR jitters on
        #      punctuation and ellipsis width all the time)
        cands: dict[str, Row] = {}
        skipped_unseen = 0
        for key, r in usable.items():
            name_key = norm(r.name)
            if name_key in self.exclude or (self.only and name_key not in self.only):
                if self.prev.get(key) != r.signature():
                    why = "在排除名单里" if name_key in self.exclude else "不在 only_from 白名单里"
                    log.debug("跳过 %s：%s（预览 %r）", r.name, why, r.preview)
                continue
            sig = r.signature()
            old = self.prev.get(key)
            preview = sig.split("|", 1)[0]

            # OCR keeps re-spelling long names with different punctuation; without this
            # every re-spelling looks like a brand-new chat and its message is skipped.
            if old is None and not key.startswith("anon:"):
                near = _nearest_key(self.prev, key)
                if near is not None:
                    old = self.prev.pop(near)
                    self.labels[key] = self.labels.pop(near, r.name.strip())
                    log.debug("%s：名字 OCR 抖动，按同一会话处理", r.name)

            if old == sig:
                continue                       # nothing changed in this row at all

            if self.require_unread and not _has_unread(arr, r, self.layout):
                self.prev[key] = sig
                log.debug("跳过 %s：没有未读红点（预览 %r）", r.name, r.preview)
                continue

            if old is None:
                # Never seen before. A new message bumps its chat to the top, but a
                # pinned chat can hold row 0 forever and a new chat then lands on row 1.
                # A fresh HH:MM timestamp tells such a row apart from an old unread
                # chat that was merely scrolled into view.
                rank = order.get(key, 99)
                if key.startswith("anon:") or rank <= 0:
                    cands[key] = r
                    log.debug("候选 %s：新出现的顶部会话", r.name)
                elif self._trust_new_row(rank, r.time):
                    cands[key] = r
                    log.debug("候选 %s：新出现的近期会话（第 %d 行，%s）",
                              r.name, rank + 1, r.time)
                else:
                    skipped_unseen += 1
                    self.prev[key] = sig
                    log.debug("跳过 %s：新出现但不在顶部（第 %d 行）", r.name, rank)
                continue

            old_preview = _canon(old.split("|", 1)[0])
            new_preview = _canon(preview)
            if old_preview == new_preview:
                self.prev[key] = sig
                log.debug("跳过 %s：只有标点/省略号差异", r.name)
                continue
            similarity = _similar(old_preview, new_preview)
            if similarity >= 0.90:
                self.prev[key] = sig
                log.debug("跳过 %s：预览几乎没变（相似度 %.2f）%r -> %r",
                          r.name, similarity, old_preview, new_preview)
                continue
            cands[key] = r
            log.debug("候选 %s：红点 + 预览变化（相似度 %.2f）%r -> %r",
                      r.name, similarity, old_preview, new_preview)
        self.last_burst = skipped_unseen
        if skipped_unseen >= 3:
            log.warning("一次出现 %d 个陌生带红点会话（可能刚解锁/重绘），请核对是否漏推",
                        skipped_unseen)
        if not cands:
            for key, r in usable.items():
                self.prev[key] = r.signature()
            return []

        # Confirm by re-capturing a fraction of a second later and comparing *pixels*
        # of that row only — no second OCR pass, so this costs milliseconds.
        time.sleep(0.3)
        img2 = self._capture()
        g1 = np.asarray(img1.convert("L"), dtype=np.int16)
        g2 = np.asarray(img2.convert("L"), dtype=np.int16)

        out: list[Change] = []
        for key, r in cands.items():
            if _band_diff(g1, g2, r, self.layout) > 1.2:
                continue          # row still repainting; the next poll will pick it up
            sig = r.signature()
            if sig == self.prev.get(key):
                continue
            seen = self.recent.setdefault(key, [])
            if sig in seen:
                self.prev[key] = sig
                continue
            seen.append(sig)
            del seen[:-6]
            self.prev[key] = sig
            out.append(Change(name=self.labels.get(key, r.name),
                              preview=r.preview, time=r.time))

        for key, r in usable.items():
            if key not in cands:
                self.prev[key] = r.signature()
        return out
