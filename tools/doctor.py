"""Environment / deployment self-check. Run this first on a new machine.

    .\\.venv\\Scripts\\python.exe tools\\doctor.py
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK = "  [OK]  "
WARN = "  [!!]  "
BAD = "  [XX]  "


def line(tag: str, text: str) -> None:
    print(tag + text)


def check_env() -> bool:
    print("== 环境 ==")
    line(OK, f"Python {sys.version.split()[0]}  ({sys.executable})")
    missing = []
    for mod in ("win32gui", "PIL", "numpy", "rapidocr_onnxruntime", "onnxruntime"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if missing:
        line(BAD, "缺少依赖：" + ", ".join(missing) + "   → 先运行 install.ps1")
        return False
    line(OK, "依赖齐全：pywin32 / pillow / numpy / rapidocr-onnxruntime")
    return True


def check_wechat():
    print("\n== 微信窗口 ==")
    from wfbot import winapi
    from wfbot.watch import SessionWatcher
    from wfbot.ocr import Ocr

    try:
        main = winapi.main_window()
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"找不到微信主窗口：{exc}")
        return None
    minimized = winapi.is_minimized(main.hwnd)
    line(OK if not minimized else BAD,
         f"主窗口 hwnd={main.hwnd} 最小化={minimized}" + ("" if not minimized else "  → 必须恢复窗口"))
    try:
        w = SessionWatcher(Ocr(), ())
        rows = w.baseline()
        lay = w.layout
        line(OK, f"窗口 {lay.w}x{lay.h}  缩放 {lay.scale:.2f}x  会话列表宽 {lay.list_right}px")
        uncertain = [r for r in rows if r.uncertain]
        line(OK if rows else BAD, f"识别到 {len(rows)} 行会话，其中 {len(uncertain)} 行名字认不出")
        for r in rows[:12]:
            tag = "?" if r.uncertain else " "
            name = r.name[:20] or "(无名)"
            print(f"        {tag} {name:<20} {r.time:10} {r.preview[:24]!r}")
        if uncertain:
            line(WARN, "名字认不出的会话建议加 2~3 字备注（单字名 OCR 读不出来）")
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"截图/识别失败：{exc}")
        return None
    return main


def check_push() -> None:
    print("\n== 推送渠道 ==")
    from wfbot.notify import Notifier

    cfg_path = ROOT / "notify-config.json"
    if not cfg_path.exists():
        line(BAD, "notify-config.json 不存在 → 运行一次 watch_notify.py 会自动生成")
        return
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    n = Notifier(cfg["channels"])
    if not n.configured:
        line(BAD, "没有配置任何渠道 → 在 notify-config.json 填 channels.bark")
        return
    line(OK, f"已配置：{n.describe()}")
    bark = cfg["channels"].get("bark", "")
    if bark:
        line(OK, f"Bark key 尾号 …{bark.rstrip('/')[-6:]}")
    line(WARN, "想确认手机能收到，运行： .\\.venv\\Scripts\\python.exe watch_notify.py --test")


def check_value_add() -> None:
    print("\n== 值守设置（需人工确认）==")
    startup = Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
    link = startup / "微信消息提醒.lnk"
    line(OK if link.exists() else WARN,
         ("已设置开机自启：" + str(link)) if link.exists()
         else "未设置开机自启（把桌面快捷方式复制到 shell:startup）")
    line(WARN, "确认已关闭自动睡眠： powercfg /change standby-timeout-ac 0")
    line(WARN, "确认已关闭锁屏（锁屏后截不到画面）")


def check_instance() -> None:
    print("\n== 运行实例 ==")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47654))
        line(WARN, "当前没有提醒进程在跑（单实例锁空闲）")
    except OSError:
        line(OK, "已有提醒进程在运行（单实例锁被占用）")
    finally:
        s.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print("微信新消息提醒 · 部署自检\n")
    ok = check_env()
    if ok:
        check_wechat()
    check_push()
    check_value_add()
    check_instance()
    print("\n自检结束。任何一行是 [XX] 就必须先修，[!!] 是建议项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
