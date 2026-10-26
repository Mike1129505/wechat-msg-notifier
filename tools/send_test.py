"""Send one message to the 大号 window to verify the whole send path.

    python tools/send_test.py
    python tools/send_test.py "自定义测试内容"
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wfbot.config import Config  # noqa: E402
from wfbot.forwarder import Forwarder  # noqa: E402
from wfbot.ocr import Ocr  # noqa: E402
from wfbot.sender import send_text  # noqa: E402
from wfbot.store import Store  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = Config.load(str(ROOT / "config.json"))
    fwd = Forwarder(cfg, Ocr(), Store(str(ROOT / "state.json")))
    win = fwd.target_window()
    if win is None:
        print("找不到大号的独立窗口，也没能自动打开。请先运行 run.py --setup")
        return 1
    text = sys.argv[1] if len(sys.argv) > 1 else f"【连通性测试】{time.strftime('%Y-%m-%d %H:%M:%S')}"
    print(f"往 {cfg.target!r} 发送：{text}")
    send_text(win, text)
    time.sleep(0.6)
    ok = fwd._verify_sent(win, text)
    print("发送成功，已在大号窗口看到这条消息" if ok else "发送后没能确认消息出现，请检查窗口状态")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
