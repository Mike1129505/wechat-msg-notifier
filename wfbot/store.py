"""Persistent per-chat state: what we already forwarded."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

MAX_SEEN = 60


@dataclass
class ChatState:
    seen: list[str] = field(default_factory=list)
    baseline_done: bool = False
    last_forward_at: str = ""
    last_media: str = ""


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self.chats: dict[str, ChatState] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name, raw in data.get("chats", {}).items():
                self.chats[name] = ChatState(
                    seen=list(raw.get("seen", []))[-MAX_SEEN:],
                    baseline_done=bool(raw.get("baseline_done", True)),
                    last_forward_at=raw.get("last_forward_at", ""),
                    last_media=raw.get("last_media", ""),
                )
        except Exception:
            self.chats = {}

    def save(self) -> None:
        data = {"chats": {k: {"seen": v.seen[-MAX_SEEN:],
                              "baseline_done": v.baseline_done,
                              "last_forward_at": v.last_forward_at,
                              "last_media": v.last_media}
                          for k, v in self.chats.items()}}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def get(self, name: str) -> ChatState:
        if name not in self.chats:
            self.chats[name] = ChatState()
        return self.chats[name]

    def mark_seen(self, name: str, fingerprints: list[str]) -> None:
        st = self.get(name)
        st.seen = (st.seen + fingerprints)[-MAX_SEEN:]
        st.baseline_done = True

    def set_baseline(self, name: str, fingerprints: list[str]) -> None:
        st = self.get(name)
        st.seen = fingerprints[-MAX_SEEN:]
        st.baseline_done = True
