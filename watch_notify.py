"""只看不碰：监视小号的会话列表，有新消息就推送到手机（Bark / Server酱 / webhook）。

不点击、不切换会话、不发送、不标记已读——只截图 + OCR。
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from wfbot.notify import Notifier  # noqa: E402
from wfbot.ocr import Ocr  # noqa: E402
from wfbot.watch import Change, NotVisible, SessionWatcher  # noqa: E402

DEFAULT_CONFIG = {
    "poll_interval": 2.5,
    "exclude": ["文件传输助手", "服务号", "公众号", "订阅号", "微信团队", "微信支付",
                "微信运动", "腾讯新闻"],
    "only_from": [],
    "quiet_hours": [],
    "per_chat_cooldown": 2,
    "detail": "brief",   # brief = 只有谁+时间；preview = 带消息预览
    "require_unread": True,   # 必须有未读红点才推送（关掉会更容易误报）
    "push_unrecognized": False,   # 名字认不出的会话也推送（默认不推，避免公众号误报）
    "log_file": "notify.log",
    "channels": {
        "bark": "",
        "ntfy": "",
        "email": {"smtp_host": "", "smtp_port": 465, "user": "", "password": "", "to": ""},
        "serverchan": "",
        "webhook": "",
        "bark_group": "微信",
    },
}

log = logging.getLogger("notify")


def load_config(path: Path) -> dict:
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("已生成默认配置 %s", path.name)
        return dict(DEFAULT_CONFIG)
    data = dict(DEFAULT_CONFIG)
    data.update(json.loads(path.read_text(encoding="utf-8")))
    return data


def setup_logging(path: str, level: str) -> None:
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s",
                                      "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(getattr(logging, level.upper(), logging.INFO))
    ch.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    logger.addHandler(ch)


def in_quiet_hours(window: list[str]) -> bool:
    if not window or len(window) != 2:
        return False
    try:
        start = datetime.strptime(window[0], "%H:%M").time()
        end = datetime.strptime(window[1], "%H:%M").time()
    except ValueError:
        return False
    now = datetime.now().time()
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def display_time(raw: str) -> str:
    """Row time is often an old date; for a fresh message just show now."""
    if re.fullmatch(r"\d{1,2}:\d{2}", raw or ""):
        return raw
    return datetime.now().strftime("%H:%M")


def format_change(c: Change) -> str:
    head = f"{c.name}"
    if c.time:
        head += f" {c.time}"
    body = c.preview or "（非文字消息）"
    return f"{head}\n{body}"


class NotifyRunner:
    def __init__(self, cfg: dict, ocr: Ocr) -> None:
        self.cfg = cfg
        self.watcher = SessionWatcher(ocr, tuple(cfg["exclude"]),
                                      tuple(cfg.get("only_from") or ()),
                                      require_unread=bool(cfg.get("require_unread", True)),
                                      push_unrecognized=bool(cfg.get("push_unrecognized", False)))
        self.notifier = Notifier(cfg["channels"])
        self.last_push: dict[str, float] = {}
        self._empty_rounds = 0
        self.pending_push: list[list] = []
        self._was_offline = False
        self.offline_after = 12      # ~18s at 1.5s polling
        self.heartbeat_path = ROOT / "heartbeat.json"
        self._hb_ts = 0.0
        self._last_burst_alert = 0.0
        self.last_push_title = ""
        self.last_push_ts = 0.0

    def _allowed(self, name: str) -> bool:
        cooldown = float(self.cfg.get("per_chat_cooldown") or 0)
        if cooldown <= 0:
            return True
        return time.time() - self.last_push.get(name, 0) >= cooldown

    def send(self, changes: list[Change]) -> None:
        fresh = [c for c in changes if self._allowed(c.name)]
        if not fresh:
            log.debug("都在冷却期内，跳过：%s", [c.name for c in changes])
            return
        detail = (self.cfg.get("detail") or "brief").lower()
        if len(fresh) == 1:
            c = fresh[0]
            title = f"微信：{c.name}"
            stamp = display_time(c.time)
            if detail == "preview":
                body = f"{c.preview or '（非文字消息）'}\n{stamp}"
            else:
                body = f"{stamp} 给你发了一条消息"
        else:
            title = f"微信：{len(fresh)} 个会话有新消息"
            if detail == "preview":
                body = "\n\n".join(format_change(c) for c in fresh)
            else:
                body = "\n".join(
                    f"{c.name} {display_time(c.time)} 发来一条消息" for c in fresh)
        ok = self.notifier.send(title, body)
        self.last_push_title = title
        self.last_push_ts = time.time()
        level = logging.INFO if ok else logging.WARNING
        log.log(level, "%s推送：%s%s", "已" if ok else "未能", title,
                "" if ok else f"（渠道：{self.notifier.describe()}），稍后补推")
        if not ok:
            self.pending_push.append([title, body, 1])
            del self.pending_push[:-20]
        for c in fresh:
            self.last_push[c.name] = time.time()
        log.debug("内容：%s", body)

    def flush_pending_pushes(self) -> None:
        for item in list(self.pending_push):
            title, body, tries = item
            if tries >= 6:
                self.pending_push.remove(item)
                log.error("放弃补推：%s", title)
                continue
            if self.notifier.send(title, body):
                self.pending_push.remove(item)
                log.info("补推成功：%s", title)
            else:
                item[2] = tries + 1

    def tick(self) -> None:
        if self.pending_push:
            self.flush_pending_pushes()
        if in_quiet_hours(self.cfg.get("quiet_hours") or []):
            self.watcher.poll()          # 仍然更新基线，避免免打扰结束后补推一堆
            return
        changes = self.watcher.poll()
        self._check_alive()
        if changes:
            self.send(changes)
        if self.watcher.last_burst >= 3:
            self._alert_burst()

    def _alert_burst(self) -> None:
        """Several badged chats appeared at once: the list probably just repainted
        (unlock / reconnect), and those messages may have been skipped."""
        now = time.time()
        n = self.watcher.last_burst
        self.watcher.last_burst = 0
        if now - self._last_burst_alert < 1800:
            return
        self._last_burst_alert = now
        log.warning("疑似漏推：本轮跳过 %d 个陌生带红点会话", n)
        self.notifier.send(f"微信：疑似漏推 {n} 条",
                           "会话列表刚发生一次大幅变化（可能是解锁屏幕或微信重绘），"
                           f"有 {n} 个带红点的陌生会话被跳过。建议打开微信核对一下。")

    def _check_alive(self, reason: str = "") -> None:
        """Detect states where new messages cannot be read: minimized, logged out,
        or the login / loading screen that OCR otherwise reports as chat rows."""
        win = self.watcher.last_rows
        overlap = self.watcher.last_overlap
        known = len(self.watcher.known)
        noisy = self.watcher.last_noise >= 3
        # When the window is gone, last_rows still holds the last good frame, so the
        # reason string is the only reliable signal.
        missing = "找不到微信主窗口" in reason or "最小化" in reason or "读不到画面" in reason
        # A login screen parses as a couple of rows ("登录", "取消", ...), so the row
        # count alone is no proof: also require overlap with chats seen before.
        abnormal = missing or win < 2 or noisy or (known >= 3 and overlap == 0)
        if not abnormal:
            if self._was_offline:
                log.info("会话列表恢复正常（小号重新登录了）")
                self._was_offline = False
            self._empty_rounds = 0
            return
        self._empty_rounds += 1
        if self._empty_rounds < self.offline_after or self._was_offline:
            return
        self._was_offline = True
        if "最小化" in reason:
            log.error("微信窗口被最小化超过 %d 轮，读不到消息", self._empty_rounds)
            self.notifier.send("微信：窗口被最小化",
                               "最小化后读不到画面，暂时收不到提醒。把微信窗口恢复出来即可，"
                               "不用重启程序。")
        elif missing:
            log.error("连续 %d 轮找不到微信窗口，小号可能已经退出登录", self._empty_rounds)
            self.notifier.send("微信：已掉线",
                               "电脑上的微信退出了/没登录，暂时收不到提醒。重新登录后我会自动继续，"
                               "不用重启程序。")
        elif noisy or win >= 2:
            log.error("连续 %d 轮识别到登录/弹窗界面，读不到会话列表", self._empty_rounds)
            self.notifier.send("微信：界面异常",
                               "识别到登录/弹窗界面（可能小号正在重新登录），期间消息会漏推。"
                               "回到微信确认一下，登录后会自动恢复。")
        else:
            log.error("连续 %d 轮读不到会话列表，小号可能已经在电脑上退出了", self._empty_rounds)
            self.notifier.send("微信：已掉线",
                               "电脑上的小号退出了，扫码重新登录后我会继续提醒。")

    def _heartbeat(self, state: str = "ok", reason: str = "") -> None:
        """Write what the loop is currently seeing; tools/selfcheck.py reads this."""
        now = time.time()
        if state == "ok" and now - self._hb_ts < 10:
            return
        self._hb_ts = now
        data = {
            "ts": now,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pid": os.getpid(),
            "state": state,
            "reason": reason,
            "rows": self.watcher.last_rows,
            "usable": self.watcher.last_usable,
            "overlap": self.watcher.last_overlap,
            "known": len(self.watcher.known),
            "known_names": sorted(self.watcher.known)[:60],
            "noise": self.watcher.last_noise,
            "last_push_title": self.last_push_title,
            "last_push_ts": self.last_push_ts,
            "pending": len(self.pending_push),
            "poll_interval": self.cfg.get("poll_interval"),
        }
        try:
            tmp = self.heartbeat_path.with_name("heartbeat.json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.heartbeat_path)
        except Exception:  # noqa: BLE001
            log.debug("写心跳失败", exc_info=True)

    def run(self, once: bool = False) -> int:
        if once:
            rows = self.watcher.baseline()
        else:
            # At logon WeChat may still be starting / waiting for a QR scan. Exiting here
            # used to kill the watcher silently; instead keep the single-instance lock and
            # retry until the window shows up.
            while True:
                try:
                    rows = self.watcher.baseline()
                    break
                except KeyboardInterrupt:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.warning("启动时读不到微信（%s），30 秒后重试；打开/登录微信后会自动继续", exc)
                    self._heartbeat("not_visible", str(exc))
                    time.sleep(30)
        log.info("已建立基线：%d 个会话（启动前的消息不会提醒）", len(rows))
        if not self.notifier.configured:
            log.warning("还没配置推送渠道，现在只会打印到日志。在 notify-config.json 里填 "
                        "channels.bark / channels.email / channels.ntfy 任意一个即可。")
        if once:
            time.sleep(self.cfg["poll_interval"])
            self.tick()
            self._heartbeat("ok")
            return 0
        while True:
            time.sleep(self.cfg["poll_interval"])
            try:
                self.tick()
                self._heartbeat("ok")
            except NotVisible as exc:
                log.warning("%s", exc)
                self._empty_rounds += 1
                self._check_alive(str(exc))
                self._heartbeat("not_visible", str(exc))
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("本轮出错：%s", exc)
                self._heartbeat("error", str(exc))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="微信小号新消息提醒（只读，不操作微信）")
    ap.add_argument("--config", default=str(ROOT / "notify-config.json"))
    ap.add_argument("--check", action="store_true", help="只打印会话列表识别结果")
    ap.add_argument("--test", action="store_true", help="发一条测试推送")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    setup_logging(cfg["log_file"], args.log_level)
    if args.interval:
        cfg["poll_interval"] = args.interval

    ocr = Ocr()
    if args.check:
        w = SessionWatcher(ocr, tuple(cfg["exclude"]), tuple(cfg.get("only_from") or ()),
                           require_unread=bool(cfg.get("require_unread", True)),
                           push_unrecognized=bool(cfg.get("push_unrecognized", False)))
        rows = w.baseline()
        lay = w.layout
        print(f"窗口 {lay.w}x{lay.h}  缩放 {lay.scale:.2f}x  会话列表宽 {lay.list_right}px")
        if abs(lay.scale - 1.0) > 0.01:
            print(f"  （检测到 {lay.scale*100:.0f}% 缩放，坐标已按比例换算）")
        for r in rows:
            mark = "排除" if r.name in cfg["exclude"] else ("不确定" if r.uncertain else "")
            print(f"  y={r.y:4d} {r.name!r:26} {r.time:10} {r.preview!r} {mark}")
        return 0

    notifier = Notifier(cfg["channels"])
    if args.test:
        print("推送渠道：", notifier.describe())
        ok = notifier.send("测试推送", f"来自 {os.environ.get('COMPUTERNAME', 'PC')} 的微信提醒测试\n"
                                     f"{datetime.now():%Y-%m-%d %H:%M:%S}")
        print("发送成功，去看手机" if ok else "发送失败，检查 key/token 是否正确")
        return 0 if ok else 2

    runner = NotifyRunner(cfg, ocr)
    lock = _single_instance_lock()
    if lock is None:
        print("已经有一个提醒程序在运行了（看任务栏『微信小号消息提醒』窗口）。")
        return 3
    try:
        return runner.run(once=args.once)
    except KeyboardInterrupt:
        print("\n已停止")
        return 0


def _single_instance_lock():
    """Keep a socket bound so a second copy exits instead of pushing duplicates."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47654))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


if __name__ == "__main__":
    sys.exit(main())
