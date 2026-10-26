"""Push channels: Bark / ntfy / e-mail / Server酱 / generic webhook.

All of them leave WeChat alone — the message is delivered by some other service.
"""
from __future__ import annotations

import json
import logging
import smtplib
import time
import urllib.parse
import urllib.request
from email.header import Header
from email.message import EmailMessage

log = logging.getLogger("wfbot")


def _header_safe(text: str) -> str:
    """HTTP headers must be latin-1; Chinese titles need RFC2047."""
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        return Header(text, "utf-8").encode()


class Notifier:
    def __init__(self, cfg: dict) -> None:
        bark = (cfg.get("bark") or "").strip()
        self.bark = "" if "YOUR_KEY" in bark else bark
        ntfy = (cfg.get("ntfy") or "").strip()
        self.ntfy = "" if "YOUR_TOPIC" in ntfy else ntfy
        self.serverchan = (cfg.get("serverchan") or "").strip()
        self.webhook = (cfg.get("webhook") or "").strip()
        self.email = cfg.get("email") or {}
        self.group = cfg.get("bark_group") or "微信"
        self.sound = cfg.get("bark_sound") or ""
        self.ttl = cfg.get("bark_ttl") or 0

    @property
    def configured(self) -> bool:
        return bool(self.bark or self.ntfy or self.serverchan or self.webhook
                    or (self.email or {}).get("smtp_host"))

    def describe(self) -> str:
        out = []
        if self.bark:
            out.append("Bark")
        if self.ntfy:
            out.append("ntfy")
        if (self.email or {}).get("smtp_host"):
            out.append("邮件")
        if self.serverchan:
            out.append("Server酱")
        if self.webhook:
            out.append("Webhook")
        return "、".join(out) or "（未配置）"

    def send(self, title: str, body: str) -> bool:
        ok = False
        for fn in (self._bark, self._ntfy, self._email, self._serverchan, self._webhook):
            if not self._enabled(fn):
                continue
            try:
                if fn(title, body):
                    ok = True
            except Exception as exc:  # noqa: BLE001
                log.warning("推送失败（%s）：%s", fn.__name__.lstrip("_"), exc)
        return ok

    def _enabled(self, fn) -> bool:
        return {
            "_bark": bool(self.bark),
            "_ntfy": bool(self.ntfy),
            "_email": bool((self.email or {}).get("smtp_host")),
            "_serverchan": bool(self.serverchan),
            "_webhook": bool(self.webhook),
        }[fn.__name__]

    # ---------------------------------------------------------------- channels
    def _bark(self, title: str, body: str) -> bool:
        base = self.bark.rstrip("/")
        url = f"{base}/{urllib.parse.quote(title, safe='')}/{urllib.parse.quote(body, safe='')}"
        q = {"group": self.group, "isArchive": "1"}
        if self.sound:
            q["sound"] = self.sound
        if self.ttl:
            q["ttl"] = int(self.ttl)
        url += "?" + urllib.parse.urlencode(q)
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    if resp.status == 200:
                        return True
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(0.6 * (attempt + 1))
        if last:
            raise last
        return False

    def _ntfy(self, title: str, body: str) -> bool:
        """ntfy.sh/<topic> — no account, no app required (web page works too)."""
        url = self.ntfy if self.ntfy.startswith("http") else f"https://ntfy.sh/{self.ntfy}"
        req = urllib.request.Request(
            url, data=body.encode("utf-8"), method="POST",
            headers={"Title": _header_safe(title), "Tags": "speech_balloon", "Priority": "default"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return 200 <= resp.status < 300

    def _email(self, title: str, body: str) -> bool:
        e = self.email or {}
        host = e.get("smtp_host")
        port = int(e.get("smtp_port") or 465)
        user = e.get("user") or ""
        pwd = e.get("password") or ""
        to = e.get("to") or user
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = e.get("from") or user
        msg["To"] = to
        msg.set_content(body)
        tls = (e.get("tls") or ("starttls" if port == 587 else "ssl")).lower()
        if tls == "none":
            with smtplib.SMTP(host, port, timeout=12) as s:
                if user:
                    s.login(user, pwd)
                s.send_message(msg)
        elif tls == "starttls":
            with smtplib.SMTP(host, port, timeout=12) as s:
                s.starttls()
                if user:
                    s.login(user, pwd)
                s.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=12) as s:
                if user:
                    s.login(user, pwd)
                s.send_message(msg)
        return True

    def _serverchan(self, title: str, body: str) -> bool:
        key = self.serverchan.rstrip("/")
        url = key if key.startswith("http") else f"https://sctapi.ftqq.com/{key}.send"
        data = urllib.parse.urlencode({"title": title, "desp": body}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200

    def _webhook(self, title: str, body: str) -> bool:
        payload = json.dumps({"title": title, "body": body}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.webhook, data=payload, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return 200 <= resp.status < 300
