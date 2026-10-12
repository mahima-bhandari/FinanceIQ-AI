"""Tiny file-based chat history, one JSON file per visitor id.

The visitor id lives in the page URL (?u=...), so a browser refresh keeps the same history.
Note: on Streamlit Community Cloud the disk is temporary - history survives refreshes and
reconnects, but is wiped when the app is rebooted or redeployed.
"""
import json
import os
import re
from pathlib import Path

DATA_DIR = Path(os.getenv("CHAT_DATA_DIR", ".chat_data"))
MAX_CHATS = 30


def _file(uid) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9]", "", str(uid))[:32] or "anon"
    return DATA_DIR / f"{safe}.json"


def load(uid) -> dict:
    try:
        data = json.loads(_file(uid).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(uid, chats: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        newest = sorted(chats.items(), key=lambda kv: kv[1].get("updated", 0), reverse=True)[:MAX_CHATS]
        target = _file(uid)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(newest), ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
    except Exception:
        pass  # never break the chat because the disk is unavailable
