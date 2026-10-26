"""Windows low-level helpers: window discovery, DPI, capture, mouse/keyboard, clipboard."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from dataclasses import dataclass
from typing import Iterable

import win32clipboard
import win32con
import win32gui
import win32process
from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

PW_RENDERFULLCONTENT = 0x00000002
SW_RESTORE = 9
SW_SHOW = 5

# Make this process per-monitor DPI aware so that GetWindowRect, PrintWindow output,
# SendInput and SetCursorPos all speak the same (physical) pixel space.
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except Exception:  # pragma: no cover
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

VK_CONTROL = 0x11
VK_MENU = 0x12
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_A = 0x41
VK_V = 0x56
VK_F = 0x46
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTunion)]


@dataclass
class WinInfo:
    hwnd: int
    title: str
    cls: str
    pid: int

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return get_window_rect(self.hwnd)

    @property
    def size(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return right - left, bottom - top


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = RECT()
    user32.GetWindowRect(int(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(int(hwnd)))


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(int(hwnd))
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(int(hwnd), buf, n + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(int(hwnd), buf, 256)
    return buf.value


def list_windows(predicate=None, include_hidden: bool = False) -> list[WinInfo]:
    out: list[WinInfo] = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _lparam):
        if not include_hidden and not user32.IsWindowVisible(hwnd):
            return True
        title = window_title(hwnd)
        cls = window_class(hwnd)
        if not title and not cls:
            return True
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        info = WinInfo(hwnd=int(hwnd), title=title, cls=cls, pid=int(pid))
        if predicate is None or predicate(info):
            out.append(info)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def wechat_windows(include_hidden: bool = False) -> list[WinInfo]:
    """All top-level Qt windows belonging to Weixin (main window and detached chats)."""
    wins = list_windows(lambda w: w.cls.startswith("Qt5") and w.cls.endswith("QWindowIcon"),
                        include_hidden=include_hidden)
    return [w for w in wins if w.title]


def main_window() -> WinInfo:
    for w in wechat_windows():
        if w.title == "微信":
            return w
    # hidden to tray: find it anyway and bring it back
    for w in wechat_windows(include_hidden=True):
        if w.title == "微信":
            user32.ShowWindow(int(w.hwnd), SW_SHOW)
            time.sleep(0.4)
            return w
    raise RuntimeError("找不到微信主窗口（标题为『微信』）。请确认微信已登录。")


def chat_windows() -> list[WinInfo]:
    """Detached chat windows (any Qt window whose title is not 微信)."""
    out = []
    for w in wechat_windows(include_hidden=True):
        if w.title != "微信" and w.title:
            out.append(w)
    return out


def restore(hwnd: int) -> None:
    if is_minimized(hwnd):
        user32.ShowWindow(int(hwnd), SW_RESTORE)
        time.sleep(0.35)


def force_foreground(hwnd: int, tries: int = 4) -> bool:
    """Bring a window to the foreground, working around the foreground lock."""
    hwnd = int(hwnd)
    restore(hwnd)
    if user32.GetForegroundWindow() == hwnd:
        return True
    for _ in range(tries):
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_cur = kernel32.GetCurrentThreadId()
        attached = False
        if tid_fg and tid_fg != tid_cur:
            attached = bool(user32.AttachThreadInput(tid_cur, tid_fg, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(tid_cur, tid_fg, False)
        time.sleep(0.12)
        if user32.GetForegroundWindow() == hwnd:
            return True
        # ALT tap is the classic unlock for SetForegroundWindow
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.12)
        if user32.GetForegroundWindow() == hwnd:
            return True
    return user32.GetForegroundWindow() == hwnd


HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


def raise_window(hwnd: int) -> None:
    user32.SetWindowPos(int(hwnd), HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    force_foreground(hwnd)


def unraise_window(hwnd: int) -> None:
    user32.SetWindowPos(int(hwnd), HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def top_window_at(x: int, y: int) -> int:
    h = user32.WindowFromPoint(POINT(int(x), int(y)))
    if not h:
        return 0
    return int(user32.GetAncestor(h, 2))  # GA_ROOT


def ensure_clickable(hwnd: int, x: int, y: int, tries: int = 4) -> bool:
    """Make sure the point really belongs to `hwnd` before clicking it."""
    hwnd = int(hwnd)
    for _ in range(tries):
        if top_window_at(x, y) == hwnd:
            return True
        raise_window(hwnd)
        time.sleep(0.18)
    return top_window_at(x, y) == hwnd


def capture(hwnd: int) -> Image.Image:
    """Capture a window with PrintWindow (works even when covered by other windows)."""
    left, top, right, bottom = get_window_rect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        raise RuntimeError(f"窗口尺寸异常: {w}x{h}")
    hdc = user32.GetWindowDC(int(hwnd))
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        user32.PrintWindow(int(hwnd), mdc, PW_RENDERFULLCONTENT)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1).copy()
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(int(hwnd), hdc)


# ---------------------------------------------------------------- input

def _send(inputs: Iterable[INPUT]) -> None:
    arr = (INPUT * len(list(inputs)))
    items = list(inputs)
    arr = (INPUT * len(items))(*items)
    user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))


def _key(vk: int, up: bool = False) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD,
                 u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, wScan=0,
                                             dwFlags=KEYEVENTF_KEYUP if up else 0,
                                             time=0, dwExtraInfo=None)))


def _uni_char(ch: str, up: bool = False) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD,
                 u=_INPUTunion(ki=KEYBDINPUT(wVk=0, wScan=ord(ch), dwFlags=flags,
                                             time=0, dwExtraInfo=None)))


def key_tap(vk: int, mods: tuple[int, ...] = ()) -> None:
    seq = [_key(m) for m in mods] + [_key(vk), _key(vk, True)] + [_key(m, True) for m in reversed(mods)]
    _send(seq)


def type_unicode(text: str) -> None:
    seq: list[INPUT] = []
    for ch in text:
        seq.append(_uni_char(ch))
        seq.append(_uni_char(ch, True))
    _send(seq)


def click(x: int, y: int, hwnd: int | None = None, double: bool = False,
          settle: float = 0.25) -> bool:
    if hwnd is not None and not ensure_clickable(hwnd, x, y):
        return False
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.03)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    if double:
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(settle)
    return True


# ---------------------------------------------------------------- clipboard

def get_clipboard_text() -> str | None:
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            time.sleep(0.05)
            continue
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                return win32clipboard.GetClipboardData(win32con.CF_TEXT)
            return None
        finally:
            win32clipboard.CloseClipboard()
    return None


def set_clipboard_text(text: str) -> bool:
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            time.sleep(0.05)
            continue
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            return True
        finally:
            win32clipboard.CloseClipboard()
    return False


class clipboard_guard:
    """Keep and restore the user's clipboard text around a paste operation."""

    def __init__(self) -> None:
        self.saved: str | None = None

    def __enter__(self):
        try:
            self.saved = get_clipboard_text()
        except Exception:
            self.saved = None
        return self

    def __exit__(self, *exc):
        try:
            if self.saved is not None:
                time.sleep(0.05)
                set_clipboard_text(self.saved)
        except Exception:
            pass
        return False


def screen_pos(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """Convert window-image coordinates to absolute screen coordinates."""
    left, top, _r, _b = get_window_rect(hwnd)
    return left + int(x), top + int(y)


def foreground_is(hwnd: int) -> bool:
    return user32.GetForegroundWindow() == int(hwnd)
