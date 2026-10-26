"""Configuration handling."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict

DEFAULT_EXCLUDE = ["文件传输助手", "服务号", "公众号", "订阅号", "微信团队", "微信支付", "微信运动", "腾讯新闻"]


@dataclass
class Config:
    target: str = "小"
    poll_interval: float = 3.0
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    startup_forward: bool = False
    max_messages_per_chat: int = 6
    max_chats_per_cycle: int = 4
    max_chars: int = 1500
    dry_run: bool = False
    log_file: str = "forwarder.log"
    state_file: str = "state.json"
    settle: float = 0.9
    max_retries: int = 6
    fail_cooldown: float = 180.0

    @classmethod
    def load(cls, path: str) -> "Config":
        if not os.path.exists(path):
            cfg = cls()
            cfg.save(path)
            return cfg
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
