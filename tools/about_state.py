"""Print the live state of WeChat windows (used by status.ps1)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wfbot import winapi  # noqa: E402
from wfbot.config import Config  # noqa: E402
from wfbot.sender import find_chat_window  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = Config.load(str(ROOT / "config.json"))
    try:
        main_win = winapi.main_window()
        mini = winapi.is_minimized(main_win.hwnd)
        print(f"  主窗口: hwnd={main_win.hwnd} 最小化={mini}")
        if mini:
            print("    ⚠ 最小化会读不到消息，程序会自己弹出来，最好别最小化")
    except Exception as exc:  # noqa: BLE001
        print(f"  主窗口: 找不到 -> {exc}")

    win = find_chat_window(cfg.target)
    if win:
        print(f"  大号窗口({cfg.target}): 就绪 hwnd={win.hwnd}")
    else:
        print(f"  大号窗口({cfg.target}): 未打开（只有转发模式才需要）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
