"""Entry point for the WeChat 小号 -> 大号 forwarder."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from wfbot import winapi
from wfbot.config import Config
from wfbot.forwarder import Forwarder
from wfbot.ocr import Ocr
from wfbot.sender import find_chat_window
from wfbot.sessionlist import parse_rows
from wfbot.store import Store

LOG_FORMAT = "%(asctime)s %(levelname)-5s %(message)s"


def setup_logging(cfg: Config, level: str) -> None:
    logger = logging.getLogger("wfbot")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fh = logging.FileHandler(cfg.log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(LOG_FORMAT, "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(getattr(logging, level.upper(), logging.INFO))
    ch.setFormatter(logging.Formatter(LOG_FORMAT, "%H:%M:%S"))
    logger.addHandler(ch)


def cmd_check(cfg: Config, ocr: Ocr) -> int:
    main = winapi.main_window()
    print(f"主窗口: hwnd={main.hwnd} rect={winapi.get_window_rect(main.hwnd)} "
          f"minimized={winapi.is_minimized(main.hwnd)}")
    target = find_chat_window(cfg.target)
    if target:
        print(f"目标窗口: hwnd={target.hwnd} title={target.title!r} "
              f"rect={winapi.get_window_rect(target.hwnd)}")
    else:
        print(f"目标窗口: 未找到标题为 {cfg.target!r} 的独立聊天窗口")
    others = [w.title for w in winapi.chat_windows()]
    print(f"当前独立聊天窗口: {others}")

    img = winapi.capture(main.hwnd)
    lines = ocr.lines(img)
    rows = parse_rows(lines, list_right=314, top=70)
    print(f"会话列表共 {len(rows)} 行：")
    for r in rows:
        flag = "  [排除]" if r.name in cfg.exclude else ""
        print(f"  y={r.y:4d} {r.name!r:22} {r.time:10} {r.preview!r}{flag}")
    return 0


def cmd_setup(cfg: Config) -> int:
    print("== 初始化向导 ==")
    print(f"1) 请在小号的微信里，双击与大号『{cfg.target}』的聊天，它会变成一个独立窗口。")
    print("2) 保持这个独立窗口打开（可以放在旁边，不要关闭）。")
    print("   正在等待该窗口出现……")
    deadline = time.time() + 300
    while time.time() < deadline:
        win = find_chat_window(cfg.target)
        if win:
            print(f"已找到大号窗口：hwnd={win.hwnd} title={win.title!r}")
            print("初始化完成，可以运行： python run.py")
            return 0
        time.sleep(1.5)
    print("超时：仍未找到该窗口。请确认窗口标题确实与配置里的 target 一致。")
    return 1


def cmd_run(cfg: Config, ocr: Ocr, once: bool) -> int:
    store = Store(cfg.state_file)
    fwd = Forwarder(cfg, ocr, store)
    if cfg.dry_run:
        logging.getLogger("wfbot").info("dry-run 模式：只打印，不真的发送")
    if find_chat_window(cfg.target) is None:
        logging.getLogger("wfbot").warning(
            "尚未发现大号『%s』的独立聊天窗口；转发时会失败。建议先运行 --setup。", cfg.target)

    if once:
        fwd.tick()          # baseline
        time.sleep(cfg.poll_interval)
        fwd.tick()          # one real pass
        return 0

    logging.getLogger("wfbot").info("开始监控，间隔 %.1fs，目标=%r，排除=%s",
                                    cfg.poll_interval, cfg.target, cfg.exclude)
    try:
        while True:
            fwd.tick()
            time.sleep(cfg.poll_interval)
    except KeyboardInterrupt:
        logging.getLogger("wfbot").info("已停止（收到 Ctrl+C）")
        print(f"\n统计：{fwd.stats}")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="微信小号消息自动转发到大号")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--once", action="store_true", help="只跑一轮检测（用于调试）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不发送")
    ap.add_argument("--interval", type=float, help="轮询间隔秒")
    ap.add_argument("--target", help="大号在小号里的显示名")
    ap.add_argument("--check", action="store_true", help="检查窗口与列表识别情况")
    ap.add_argument("--setup", action="store_true", help="初始化：等待大号独立窗口")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    cfg = Config.load(args.config)
    if args.dry_run:
        cfg.dry_run = True
    if args.interval:
        cfg.poll_interval = args.interval
    if args.target:
        cfg.target = args.target
    setup_logging(cfg, args.log_level)

    if args.setup:
        return cmd_setup(cfg)

    ocr = Ocr()
    if args.check:
        return cmd_check(cfg, ocr)
    return cmd_run(cfg, ocr, args.once)


if __name__ == "__main__":
    sys.exit(main())
