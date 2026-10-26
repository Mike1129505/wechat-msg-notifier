"""微信新消息提醒 · 独立自检

每 10 分钟跑一次（tools/install_selfcheck.ps1 会注册计划任务）。它做三件事：
  1. 确认主程序还活着，并且心跳是新鲜的（不是卡死）
  2. 自己截一次微信窗口，确认画面没黑、会话列表认得出来、没有登录/弹窗界面
  3. 扫最近的日志，发现"疑似漏推"就写下来，必要时通过 Bark 通知手机

用法：
    .\\.venv\\Scripts\\python.exe tools\\selfcheck.py            # 人工看结果
    .\\.venv\\Scripts\\python.exe tools\\selfcheck.py --notify   # 有问题才推手机
    .\\.venv\\Scripts\\python.exe tools\\selfcheck.py --notify --restart  # 计划任务用
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORT = 47654
HEARTBEAT = ROOT / "heartbeat.json"
STATE_FILE = ROOT / "selfcheck-state.json"
LOG_FILE = ROOT / "selfcheck.log"

OK = "  [OK]  "
WARN = "  [!!]  "
BAD = "  [XX]  "

UI_WORDS = {"登录", "取消", "确定", "正在进入", "收取中", "扫码登录", "切换账号",
            "进入微信", "退出登录", "进入", "加载中"}

results: list[tuple[str, str, str]] = []      # (level, key, text)


def add(level: str, key: str, text: str) -> None:
    results.append((level, key, text))


def log_line(text: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {text}\n")
    except OSError:
        pass


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def port_busy() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", PORT))
        return False
    except OSError:
        return True
    finally:
        s.close()


def port_pid() -> int | None:
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             encoding="mbcs", errors="ignore", timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if f":{PORT}" in line and "LISTENING" in line.upper():
            try:
                return int(line.split()[-1])
            except ValueError:
                return None
    return None


def check_heartbeat() -> dict | None:
    hb = load_json(HEARTBEAT)
    if not hb:
        add(BAD, "heartbeat", "heartbeat.json 不存在（主程序可能从没跑起来）")
        return None
    age = time.time() - float(hb.get("ts") or 0)
    if age > 180:
        add(BAD, "heartbeat", f"心跳已 {age/60:.1f} 分钟没更新，主程序可能卡死")
    elif age > 60:
        add(WARN, "heartbeat", f"心跳 {age:.0f} 秒没更新（正常约 10 秒一次）")
    else:
        add(OK, "heartbeat", f"心跳正常（{age:.0f} 秒前，pid={hb.get('pid')}）")
    if hb.get("state") and hb["state"] != "ok":
        add(BAD, "heartbeat_state", f"主程序状态：{hb['state']} {hb.get('reason') or ''}".strip())
    return hb


def check_window(cfg: dict, hb: dict | None) -> None:
    try:
        from wfbot import winapi
    except Exception as exc:  # noqa: BLE001
        add(BAD, "window", f"导入 winapi 失败：{exc}")
        return
    try:
        main = winapi.main_window()
    except Exception as exc:  # noqa: BLE001
        add(BAD, "window", f"找不到微信主窗口：{exc}")
        return
    minimized = winapi.is_minimized(main.hwnd)
    if minimized:
        add(BAD, "window", f"微信主窗口被最小化（hwnd={main.hwnd}），读不到画面")
        return
    add(OK, "window", f"微信主窗口正常（hwnd={main.hwnd}，未最小化）")

    try:
        import numpy as np
        img = winapi.capture(main.hwnd)
        luma = float(np.asarray(img.convert("L"), dtype=np.float64).mean())
        if luma < 20:
            add(BAD, "capture", f"截图几乎全黑（亮度 {luma:.0f}）→ 可能锁屏或窗口未渲染")
            return
        add(OK, "capture", f"截图正常（{img.width}x{img.height}，亮度 {luma:.0f}）")
    except Exception as exc:  # noqa: BLE001
        add(BAD, "capture", f"截图失败：{exc}")
        return

    try:
        from wfbot.ocr import Ocr
        from wfbot.watch import SessionWatcher
        watcher = SessionWatcher(Ocr(), tuple(cfg.get("exclude") or ()),
                                 push_unrecognized=bool(cfg.get("push_unrecognized", False)))
        rows = watcher.baseline()
    except Exception as exc:  # noqa: BLE001
        add(BAD, "ocr", f"识别会话列表失败：{exc}")
        return

    named = [r for r in rows if r.name and not r.uncertain]
    noise = [r for r in named if r.name.strip() in UI_WORDS and not (r.preview or r.time)]
    if len(named) < 2:
        add(BAD, "ocr", f"只认出 {len(named)} 行会话（正常 5~9 行）")
    else:
        add(OK, "ocr", f"认出 {len(named)} 行会话，其中 {len(noise)} 行是登录/弹窗文字")
    if noise:
        add(BAD if len(noise) >= 2 else WARN, "login",
            "识别到登录/弹窗界面文字：" + "、".join(r.name.strip() for r in noise[:5]))

    junk = [r.name.strip() for r in named
            if "@" in r.name or "昨天" in r.name or "今天" in r.name
            or re.search(r"[.．…·]$", r.name.strip())]
    if junk:
        add(WARN, "ocr_names", f"{len(junk)} 个会话名还带 OCR 噪声："
                                + "、".join(junk[:4]))

    known = set(hb.get("known_names") or []) if hb else set()
    if known:
        names = {r.name.strip() for r in named}
        overlap = names & known
        if overlap:
            add(OK, "overlap", f"和已知会话重合 {len(overlap)} 个（{', '.join(sorted(overlap)[:3])}…）")
        else:
            add(BAD, "overlap", "一个已知会话都没认出来 → 微信可能未登录或被全屏弹窗挡住")


def check_channel(cfg: dict) -> None:
    try:
        from wfbot.notify import Notifier
        n = Notifier(cfg.get("channels") or {})
    except Exception as exc:  # noqa: BLE001
        add(BAD, "channel", f"推送渠道初始化失败：{exc}")
        return
    if not n.configured:
        add(BAD, "channel", "没有配置任何推送渠道（Bark/邮件/ntfy/webhook 都空）")
    else:
        add(OK, "channel", f"推送渠道已配置：{n.describe()}")


def recent_log_lines(minutes: int = 60) -> list[tuple[datetime, str]]:
    path = ROOT / "notify.log"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-4000:]
    except OSError:
        return []
    cutoff = datetime.now() - timedelta(minutes=minutes)
    out = []
    for line in lines:
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ts >= cutoff:
            out.append((ts, line))
    return out


def check_log() -> None:
    lines = recent_log_lines(60)
    if not lines:
        add(OK, "log", "最近 1 小时日志里没有记录（可能一直没新消息）")
        return
    bad = [l for _, l in lines if " ERROR " in l or " WARNING " in l]
    if bad:
        add(WARN, "log", f"最近 1 小时有 {len(bad)} 条警告/错误，最后一条：{bad[-1][:120]}")
    else:
        add(OK, "log", f"最近 1 小时 {len(lines)} 条日志，没有警告/错误")

    # A burst of unseen badged chats usually means the list just repainted after a
    # lock/unlock; some of those messages may have been skipped.
    skips = [(ts, l) for ts, l in lines if "新出现但不在顶部" in l]
    burst = 0
    for i, (ts, _) in enumerate(skips):
        n = sum(1 for ts2, _ in skips[i:] if (ts2 - ts).total_seconds() <= 60)
        burst = max(burst, n)
    if burst >= 3:
        add(WARN, "burst", f"日志里出现 {burst} 个「新出现但不在顶部」集中在 1 分钟内，可能有消息被跳过")

    # Candidates that never show up in any push are worth a second look.
    pushed_blob = "\n".join(l for _, l in lines if "已推送" in l or "内容：" in l)
    missing = []
    for _, line in lines:
        m = re.search(r"候选 (.+?)：", line)
        if m and m.group(1).strip() and m.group(1) not in pushed_blob:
            missing.append(m.group(1))
    if missing:
        uniq = sorted(set(missing))
        add(WARN, "candidates", f"最近 1 小时有 {len(uniq)} 个候选没出现在推送记录里："
                                + "、".join(uniq[:4]))

    # Chats that stay silent because they are on the exclude list (大号/文件传输助手 are
    # common self-test targets). Official accounts are expected noise, so skip them.
    official = {"公众号", "服务号", "订阅号", "微信团队", "微信支付", "微信运动",
                "腾讯新闻", "文件传输助手"}
    excluded_names = []
    for _, line in lines:
        m = re.search(r"跳过 (.+?)：在排除名单里", line)
        if m and m.group(1).strip() not in official:
            excluded_names.append(m.group(1).strip())
    if excluded_names:
        uniq = sorted(set(excluded_names))
        add(WARN, "excluded", "最近 1 小时这些会话有新消息但被排除名单挡住了："
                              + "、".join(uniq[:4]))


def alert(key: str, title: str, body: str, cfg: dict, cooldown: int = 1800) -> bool:
    state = load_json(STATE_FILE)
    now = time.time()
    if now - float((state.get("alerts") or {}).get(key) or 0) < cooldown:
        return False
    try:
        from wfbot.notify import Notifier
        ok = Notifier(cfg.get("channels") or {}).send(title, body)
    except Exception:  # noqa: BLE001
        ok = False
    if ok:
        state.setdefault("alerts", {})[key] = now
        save_state(state)
    return ok


def restart_watcher() -> bool:
    pid = port_pid()
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       text=True, encoding="mbcs", errors="ignore")
        time.sleep(2)
    try:
        os.startfile(str(ROOT / "notify.bat"))  # noqa: S606 - same as double-clicking it
    except OSError:
        return False
    for _ in range(20):
        time.sleep(1)
        if port_busy():
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="微信提醒自检")
    ap.add_argument("--notify", action="store_true", help="发现 [XX] 时推手机")
    ap.add_argument("--restart", action="store_true", help="主程序没跑/卡死时自动重启")
    ap.add_argument("--quiet", action="store_true", help="只在有问题时输出")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    cfg = load_json(ROOT / "notify-config.json")

    busy = port_busy()
    if busy:
        add(OK, "process", f"提醒进程在运行（端口 {PORT} 已占用，pid={port_pid()}）")
    else:
        add(BAD, "process", f"提醒进程没在运行（端口 {PORT} 空闲）")

    hb = check_heartbeat() if busy else None
    # A logged-out WeChat is already reported by the watcher itself (one "已掉线" push).
    # Don't add a second alert stream while it waits for the user to log back in.
    if hb and hb.get("state") == "not_visible":
        add(WARN, "window", "微信当前未登录/未打开；主程序在等待，掉线提醒已发过一条")
    else:
        check_window(cfg, hb)
    check_channel(cfg)
    check_log()

    bad = [r for r in results if r[0] == BAD]
    warns = [r for r in results if r[0] == WARN]

    # Auto-heal: only when the process is gone or the heartbeat is clearly stale.
    if args.restart:
        stale = hb is not None and time.time() - float(hb.get("ts") or 0) > 600
        if not busy or stale:
            log_line(f"自动重启：busy={busy} stale={stale}")
            if restart_watcher():
                add(OK, "restart", "已自动重启提醒进程")
                bad = [r for r in results if r[0] == BAD and r[1] != "process"]
            else:
                add(BAD, "restart", "自动重启失败，请手动双击桌面「微信消息提醒」")

    if not args.quiet or bad:
        print("微信提醒 · 自检\n")
        for level, _key, text in results:
            print(level + text)
        print("\n" + ("发现问题，需要处理。" if bad else
                      ("一切正常（有 %d 条提示）。" % len(warns) if warns else "一切正常。")))

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line(f"{'BAD' if bad else 'OK'} | " + " | ".join(t for _, _, t in results))

    # If WeChat is sitting on its login screen, the watcher already pushed one
    # "界面异常/已掉线" notice. Don't repeat it every 10 minutes.
    login_state = bool(hb and int(hb.get("known") or 0) >= 3
                       and int(hb.get("overlap") or 0) == 0)
    notify_bad = [r for r in bad
                  if not (login_state and r[1] in {"overlap", "login", "ocr",
                                                   "capture", "window"})]

    if notify_bad and args.notify:
        lines = "\n".join(f"· {text}" for _, _, text in notify_bad)
        alert("selfcheck", "微信提醒自检：异常", lines + f"\n\n{stamp}", cfg)

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
