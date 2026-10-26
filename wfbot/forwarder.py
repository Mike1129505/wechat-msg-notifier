"""The forwarding loop: watch the session list, open changed chats, relay to the target."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import numpy as np

from . import winapi
from .chatview import Chat, Message, parse_chat
from .config import Config
from .ocr import Line, Ocr
from .sender import find_chat_window, send_text
from .sessionlist import Row, parse_rows
from .store import Store

LIST_BOX = (0, 70, 314, 648)
CHAT_BOX = (318, 34, 896, 500)
GROUP_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")
MEDIA_RE = re.compile(r"\[(图片|动画表情|表情|视频|语音|文件|链接|小程序|音乐|位置|名片|红包|转账|聊天记录|接龙)[^\]]*\]")

log = logging.getLogger("wfbot")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


@dataclass
class Stats:
    cycles: int = 0
    chats_opened: int = 0
    forwarded: int = 0
    errors: int = 0


class Forwarder:
    def __init__(self, cfg: Config, ocr: Ocr, store: Store) -> None:
        self.cfg = cfg
        self.ocr = ocr
        self.store = store
        self.prev: dict[str, str] | None = None
        self.stats = Stats()
        self._last_target_warn = 0.0
        self.pending: dict[str, tuple[Row, Chat, list[Message], int]] = {}
        self._warned_uncertain: set[str] = set()
        self.selected_name = ""
        self.stable: dict[str, str] = {}
        self.cooldown: dict[str, float] = {}
        self.fail_count: dict[str, int] = {}

    # ---------------------------------------------------------------- helpers
    def _capture_main(self) -> tuple[winapi.WinInfo, object]:
        main = winapi.main_window()
        if winapi.is_minimized(main.hwnd):
            log.info("主窗口被最小化，正在恢复（否则无法读取画面）")
            winapi.restore(main.hwnd)
            time.sleep(0.4)
        img = winapi.capture(main.hwnd)
        return main, img

    def _blocked(self) -> set[str]:
        return {_norm(x) for x in list(self.cfg.exclude) + [self.cfg.target]}

    def snapshot_rows(self, img) -> list[Row]:
        lines = self._ocr_box(img, LIST_BOX)
        rows = parse_rows(lines, list_right=314, top=0)
        arr = np.array(img.convert("RGB"))
        band = self._selected_band(arr)
        forced = row_name_from_title(self.selected_name)
        if forced and band:
            # The highlighted row is painted white-on-green and OCR reads its name
            # inconsistently (张三 -> 张二 ...). We know which chat we opened last,
            # so pin that row to the verified name.
            for r in rows:
                yc = r.y + max(r.height, 20) / 2
                if band[0] <= yc <= band[1]:
                    r.name, r.uncertain = forced, False
        elif self.selected_name:
            for r in rows:
                if r.uncertain and self._row_selected(arr, r):
                    r.name, r.uncertain = forced, False
        return rows

    @staticmethod
    def _selected_band(arr) -> tuple[int, int] | None:
        h, w, _ = arr.shape
        x = min(300, w - 1)
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

    @staticmethod
    def _row_selected(arr, row: Row) -> bool:
        y = min(max(row.y + 8, 0), arr.shape[0] - 1)
        px = arr[y, min(305, arr.shape[1] - 1)]
        r, g, b = int(px[0]), int(px[1]), int(px[2])
        return g > 150 and g - r > 25 and g - b > 35

    def _ocr_box(self, img, box) -> list[Line]:
        crop = img.crop(box)
        lines = self.ocr.lines(crop)
        ox, oy = box[0], box[1]
        for l in lines:
            l.x += ox
            l.y += oy
        return lines

    def read_title(self, img) -> str:
        lines = self._ocr_box(img, (318, 30, 700, 80))
        best = None
        for l in lines:
            if best is None or l.x < best.x:
                best = l
        return (best.text.strip() if best else "")

    def read_chat(self, main, img) -> Chat:
        lines = self._ocr_box(img, CHAT_BOX)
        return parse_chat(lines, img, area_left=CHAT_BOX[0], area_top=CHAT_BOX[1],
                          title_bottom=74, area_bottom=CHAT_BOX[3])

    # ---------------------------------------------------------------- actions
    def _chat_area_diff(self, a, b) -> float:
        import numpy as np
        ca = np.array(a.crop(CHAT_BOX).convert("L"), dtype=int)
        cb = np.array(b.crop(CHAT_BOX).convert("L"), dtype=int)
        if ca.shape != cb.shape:
            return 999.0
        return float(np.abs(ca - cb).mean())

    def open_chat(self, main, name: str):
        """Click the row for `name`; return a capture taken after the panel rendered."""
        img0 = winapi.capture(main.hwnd)
        rows = self.snapshot_rows(img0)
        target = _norm(name)
        row = next((r for r in rows if _norm(r.name) == target), None)
        if row is None:
            row = next((r for r in rows if target and target in _norm(r.name)), None)
        if row is None:
            log.debug("会话列表里找不到 %r（可能被新消息挤出可视区）", name)
            return None
        sx, sy = winapi.screen_pos(main.hwnd, 200, row.y + 12)
        if not winapi.force_foreground(main.hwnd):
            log.warning("无法把微信主窗口切到前台")
        time.sleep(0.12)
        if not winapi.click(sx, sy, hwnd=main.hwnd, settle=0.0):
            log.warning("点击被其他窗口遮挡，未生效")
            return None

        img = img0
        for _ in range(9):
            time.sleep(0.25)
            img = winapi.capture(main.hwnd)
            if self._chat_area_diff(img0, img) > 0.8:
                break
        else:
            log.debug("点击 %r 后聊天区没有变化（可能已停留在该会话，或界面未响应）", name)
        winapi.unraise_window(main.hwnd)
        return img

    def target_window(self):
        win = find_chat_window(self.cfg.target)
        if win is not None:
            return win
        win = self.spawn_target_window()
        if win is not None:
            return win
        now = time.time()
        if now - self._last_target_warn > 60:
            self._last_target_warn = now
            log.error("找不到大号『%s』的独立聊天窗口，也无法自动创建。请在微信里双击与大号的聊天，"
                      "让它成为一个独立窗口，然后保持打开。", self.cfg.target)
        return None

    def spawn_target_window(self):
        """Double-click the target's session row to open it as a detached window."""
        try:
            main = winapi.main_window()
            img = winapi.capture(main.hwnd)
            rows = self.snapshot_rows(img)
            target = _norm(self.cfg.target)
            row = next((r for r in rows if _norm(r.name) == target), None)
            if row is None:
                log.debug("会话列表里没有 %r，无法自动开窗", self.cfg.target)
                return None
            log.info("大号窗口不在了，尝试从会话列表重新打开 %r", self.cfg.target)
            sx, sy = winapi.screen_pos(main.hwnd, 200, row.y + 12)
            winapi.force_foreground(main.hwnd)
            time.sleep(0.1)
            work = winapi.WinInfo(hwnd=main.hwnd, title=main.title, cls=main.cls, pid=main.pid)
            winapi.click(sx, sy, hwnd=main.hwnd, double=True, settle=0.4)
            winapi.unraise_window(main.hwnd)
            for _ in range(12):
                time.sleep(0.25)
                win = find_chat_window(self.cfg.target)
                if win is not None:
                    log.info("已重新打开大号窗口：hwnd=%s", win.hwnd)
                    return win
        except Exception as exc:  # noqa: BLE001
            log.warning("自动打开大号窗口失败：%s", exc)
        return None

    def forward(self, chat: Chat, row: Row, msgs: list[Message]) -> bool:
        text = self.format_batch(chat, row, msgs)
        if self.cfg.dry_run:
            log.info("[dry-run] 将转发 → %s\n%s", self.cfg.target, _indent(text))
            return True
        win = self.target_window()
        if win is None:
            return False
        ok = False
        for attempt in range(2):
            try:
                send_text(win, text)
                time.sleep(0.5)
                ok = self._verify_sent(win, text)
            except Exception as exc:  # noqa: BLE001
                log.warning("发送异常（第 %d 次）：%s", attempt + 1, exc)
            if ok:
                break
            time.sleep(0.6)
        if ok:
            self.stats.forwarded += 1
            log.info("已转发【%s】%d 条 → %s", chat.title, len(msgs), self.cfg.target)
            log.debug("内容：\n%s", _indent(text))
        else:
            log.error("转发失败（未能确认消息出现在大号窗口），稍后重试")
        return ok

    def _verify_sent(self, win, text: str) -> bool:
        img = winapi.capture(win.hwnd)
        lines = self.ocr.lines(img)
        tail = _norm(text)[-14:]
        head = _norm(text)[:10]
        for l in lines:
            t = _norm(l.text)
            if head and head in t:
                return True
            if tail and tail in t:
                return True
        return False

    def format_batch(self, chat: Chat, row: Row, msgs: list[Message]) -> str:
        stamp = row.time or time.strftime("%H:%M")
        title = GROUP_SUFFIX_RE.sub("", chat.title).strip() or chat.title
        head = f"【{title}】{stamp}"
        body_lines = []
        for m in msgs:
            prefix = ""
            if chat.is_group and m.sender:
                prefix = f"{m.sender}："
            body_lines.append(prefix + m.text)
        body = "\n".join(body_lines)
        text = f"{head}\n{body}"
        if len(text) > self.cfg.max_chars:
            text = text[: self.cfg.max_chars] + "…（已截断）"
        return text

    # ---------------------------------------------------------------- cycle
    def tick(self) -> None:
        self.stats.cycles += 1
        main, img = self._capture_main()
        rows = self.snapshot_rows(img)
        if len(rows) < 2:
            log.debug("会话列表只识别到 %d 行（可能处于搜索/其他视图），跳过本轮", len(rows))
            return

        if self.pending:
            try:
                self.flush_pending()
            except Exception as exc:  # noqa: BLE001
                log.exception("重试队列处理失败：%s", exc)

        if self.prev is None:
            if not self.selected_name:
                self.selected_name = row_name_from_title(self.read_title(img))
            self.prev = {_norm(r.name): r.signature() for r in rows}
            log.info("已建立基线：%d 个会话（启动前的历史消息不会转发）", len(rows))
            return

        prev = self.prev
        now = time.time()
        ready: list[Row] = []
        for r in rows:
            key = _norm(r.name)
            if key in self._blocked():
                continue
            if r.uncertain:
                if r.reason == "unknown" and key not in self._warned_uncertain:
                    self._warned_uncertain.add(key)
                    log.warning("有一行会话名 OCR 不出来（显示为 %r），这类行先跳过；"
                                "给它设个两三个字的备注名就能正常转发。", r.name)
                continue
            sig = r.signature()
            if prev.get(key) == sig:
                self.stable.pop(key, None)
                continue
            if self.cooldown.get(key, 0) > now:
                continue
            if self.stable.get(key) == sig:
                # the same new preview seen twice in a row: the list has settled
                ready.append(r)
            else:
                self.stable[key] = sig

        if not ready:
            return

        log.info("检测到 %d 个会话有新动静：%s", len(ready),
                 "、".join(r.name for r in ready[:6]))
        for row in ready[: self.cfg.max_chats_per_cycle]:
            key = _norm(row.name)
            sig = row.signature()
            self.stable.pop(key, None)
            self.prev[key] = sig
            try:
                self.handle_chat(main, row)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors += 1
                log.exception("处理会话 %r 出错：%s", row.name, exc)

        # Opening a chat re-renders its row (green highlight, white text) which changes what
        # OCR reads; re-baseline the rows we touched so we do not chase our own tail.
        try:
            img2 = winapi.capture(main.hwnd)
            rows2 = self.snapshot_rows(img2)
            for r in rows2:
                if not r.uncertain:
                    self.prev[_norm(r.name)] = r.signature()
        except Exception:  # noqa: BLE001
            pass

    def open_and_read(self, main, row: Row, attempts: int = 3):
        """Open the chat, verify it really is the intended one, then parse it."""
        for attempt in range(attempts):
            img = self.open_chat(main, row.name)
            if img is None:
                time.sleep(0.3)
                continue
            chat = self.read_chat(main, img)
            if self._matches(chat, row):
                if attempt:
                    log.info("第 %d 次尝试终于打开了 %r", attempt + 1, row.name)
                return img, chat
            log.info("打开的会话是 %r，与预期 %r 不符，重试", chat.title or "(读不到标题)", row.name)
            time.sleep(0.35)
        log.warning("连续 %d 次都没能正确打开 %r，跳过本轮", attempts, row.name)
        return None, None

    @staticmethod
    def _matches(chat: Chat, row: Row) -> bool:
        want = _norm(row.name)
        title = _norm(chat.title)
        if len(title) >= 2:
            return want in title or title in want
        # Title unreadable (single-character names): fall back to the preview text.
        prev = _preview_text(row.preview)
        if prev:
            body = _norm("".join(m.text for m in chat.messages))
            return prev[:5] in body
        return True

    def handle_chat(self, main, row: Row) -> None:
        img, chat = self.open_and_read(main, row)
        if chat is None:
            self.stats.errors += 1
            key = _norm(row.name)
            n = self.fail_count.get(key, 0) + 1
            self.fail_count[key] = n
            if n >= 2:
                self.cooldown[key] = time.time() + self.cfg.fail_cooldown
                self.fail_count[key] = 0
                log.warning("『%s』连续打不开（会话列表在频繁重排），先歇 %d 秒再看",
                            row.name, int(self.cfg.fail_cooldown))
            return
        self.fail_count.pop(_norm(row.name), None)
        self.stats.chats_opened += 1
        self.selected_name = row_name_from_title(chat.title) or row.name

        fps = [m.fingerprint() for m in chat.messages]
        st = self.store.get(row.name)
        new_msgs = diff_new(st.seen, chat.messages)

        media_sig = ""
        if not new_msgs:
            media = MEDIA_RE.search(row.preview)
            if media:
                media_sig = f"{row.preview}|{row.time}"
                if st.last_media == media_sig:
                    log.debug("%r 的这条媒体消息已经转过了，跳过", row.name)
                    return
                text = "[" + media.group(1) + "]"
                sender = ""
                m = re.match(r"^(.*?)：" + re.escape(media.group(0)), row.preview)
                if chat.is_group and m:
                    sender = m.group(1)
                new_msgs = [Message(text=text, direction="in", sender=sender)]
            else:
                log.debug("%r 无新内容（预览：%r）", row.name, row.preview)
                self.store.set_baseline(row.name, fps)
                return

        new_msgs = [m for m in new_msgs if m.direction == "in"]
        if not new_msgs:
            self.store.set_baseline(row.name, fps)
            return

        if len(new_msgs) > self.cfg.max_messages_per_chat:
            new_msgs = new_msgs[-self.cfg.max_messages_per_chat:]

        if self.forward(chat, row, new_msgs):
            self.store.mark_seen(row.name, [m.fingerprint() for m in new_msgs])
            if media_sig:
                st.last_media = media_sig
            st.last_forward_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self.store.save()
        else:
            self.pending[row.name] = (row, chat, new_msgs, 0)
            log.info("已排入重试队列（%r）", row.name)

    def flush_pending(self) -> None:
        for name, (row, chat, msgs, tries) in list(self.pending.items()):
            if tries >= self.cfg.max_retries:
                log.error("放弃转发 %r 的 %d 条消息（重试 %d 次仍失败）", name, len(msgs), tries)
                self.pending.pop(name, None)
                self.store.set_baseline(name, [m.fingerprint() for m in chat.messages])
                self.store.save()
                continue
            self.pending[name] = (row, chat, msgs, tries + 1)
            if self.forward(chat, row, msgs):
                self.pending.pop(name, None)
                self.store.mark_seen(name, [m.fingerprint() for m in msgs])
                self.store.get(name).last_forward_at = time.strftime("%Y-%m-%d %H:%M:%S")
                self.store.save()


def diff_new(seen: list[str], msgs: list[Message]) -> list[Message]:
    fps = [m.fingerprint() for m in msgs]
    for k in range(min(len(seen), len(fps)), 0, -1):
        if seen[-k:] == fps[:k]:
            return msgs[k:]
    known = set(seen)
    return [m for m in msgs if m.fingerprint() not in known]


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def row_name_from_title(title: str) -> str:
    """Turn a chat title ("GBUS 4330 小组 (7)~") into the session row name."""
    t = (title or "").strip()
    t = re.sub(r"[\\(（]\d+[\\)）].*$", "", t).strip()
    return t.rstrip("~～·.。 ").strip()


def _preview_text(preview: str) -> str:
    """Strip a leading 'sender：' from a session preview and normalise it."""
    m = re.match(r"^[^：:]{1,24}[：:](.+)$", preview or "")
    return _norm(m.group(1) if m else preview)
