import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Any

BASE_DIR = os.getcwd()
STORE_FILE = os.path.join(BASE_DIR, "web_link_keys.json")
# 未使用キーの保持上限（ログイン不要で発行できるため、大量発行でディスクを埋められないようにする）
MAX_KEYS = 1000

# Bot（/web applykey）と Web API（キー発行）は別スレッドで動くため、読み書きをまとめて直列化する
_store_lock = threading.RLock()


class LinkKeyStoreFullError(Exception):
    """未使用キーが上限に達している"""


def _load_store() -> dict[str, Any]:
    if not os.path.exists(STORE_FILE):
        return {}
    try:
        with open(STORE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_store(data: dict[str, Any]) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=BASE_DIR, prefix=".web_link_keys.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STORE_FILE)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _now_utc() -> datetime:
    return datetime.utcnow()


def _is_expired(expires_at_iso: str) -> bool:
    try:
        return _now_utc() >= datetime.fromisoformat(expires_at_iso)
    except Exception:
        return True


def cleanup_expired_keys() -> None:
    with _store_lock:
        _cleanup_expired_keys_locked()


def _cleanup_expired_keys_locked() -> None:
    store = _load_store()
    if not store:
        return
    changed = False
    for k in list(store.keys()):
        exp = str(store[k].get("expires_at", ""))
        if _is_expired(exp):
            del store[k]
            changed = True
    if changed:
        _save_store(store)


def create_link_key(payload: dict[str, Any], ttl_hours: int = 24) -> tuple[str, str]:
    with _store_lock:
        return _create_link_key_locked(payload, ttl_hours)


def _create_link_key_locked(payload: dict[str, Any], ttl_hours: int) -> tuple[str, str]:
    _cleanup_expired_keys_locked()
    store = _load_store()
    if len(store) >= MAX_KEYS:
        raise LinkKeyStoreFullError("未使用の連携キーが上限に達しています")

    # Human-friendly key: 10 chars uppercase+digits
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        key = "".join(secrets.choice(alphabet) for _ in range(10))
        if key not in store:
            break

    created_at = _now_utc()
    expires_at = created_at + timedelta(hours=max(1, int(ttl_hours)))

    store[key] = {
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "payload": payload,
    }
    _save_store(store)
    return key, expires_at.isoformat()


def consume_link_key(key: str) -> dict[str, Any] | None:
    with _store_lock:
        return _consume_link_key_locked(key)


def _consume_link_key_locked(key: str) -> dict[str, Any] | None:
    _cleanup_expired_keys_locked()
    store = _load_store()
    item = store.get(key)
    if not item:
        return None

    exp = str(item.get("expires_at", ""))
    if _is_expired(exp):
        del store[key]
        _save_store(store)
        return None

    payload = item.get("payload") or {}
    del store[key]
    _save_store(store)
    return payload
