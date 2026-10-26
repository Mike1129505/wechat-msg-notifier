"""Driving the WeChat UI: discover chat windows, send text, open chats."""
from __future__ import annotations

import time
from dataclasses import dataclass

from PIL import Image

from . import winapi
from .ocr import Line, Ocr

INPUT_BOX = "input"


def find_chat_window(title: str) -> winapi.WinInfo | None:
    for w in winapi.chat_windows():
        if w.title == title:
            return w
    return None


def _input_point(win: winapi.WinInfo, img: Image.Image) -> tuple[int, int]:
    """Click target inside the message input box (image coords)."""
    w, h = img.size
    return int(w * 0.45), int(h - 108)


def send_text(win: winapi.WinInfo, text: str, settle: float = 0.45) -> bool:
    """Paste text into the chat window's input box and press Enter."""
    if not winapi.force_foreground(win.hwnd):
        raise RuntimeError("无法把微信聊天窗口切到前台")
    time.sleep(0.2)

    img = winapi.capture(win.hwnd)
    ix, iy = _input_point(win, img)
    sx, sy = winapi.screen_pos(win.hwnd, ix, iy)
    winapi.click(sx, sy, hwnd=win.hwnd, settle=0.2)

    with winapi.clipboard_guard():
        if not winapi.set_clipboard_text(text):
            raise RuntimeError("写入剪贴板失败")
        winapi.key_tap(winapi.VK_V, (winapi.VK_CONTROL,))
        time.sleep(0.35)
        winapi.key_tap(winapi.VK_RETURN)
    time.sleep(settle)
    return True


def send_and_verify(win: winapi.WinInfo, text: str, ocr: Ocr) -> bool:
    send_text(win, text)
    time.sleep(0.6)
    img = winapi.capture(win.hwnd)
    lines = ocr.lines(img)
    probe = _norm(text)[:12]
    for l in lines:
        if probe and probe in _norm(l.text):
            return True
    return False


def _norm(s: str) -> str:
    return "".join(ch for ch in s if not ch.isspace())


def open_chat_from_row(win: winapi.WinInfo, row_y: int, x_img: int = 200,
                       detached: bool = False) -> None:
    """Click (or double-click) a session row in the main window."""
    winapi.force_foreground(win.hwnd)
    sx, sy = winapi.screen_pos(win.hwnd, x_img, row_y)
    winapi.click(sx, sy, hwnd=win.hwnd, double=detached, settle=0.9 if detached else 0.7)


@dataclass
class ChatSnapshot:
    title: str
    lines: list[Line]
    image: Image.Image
