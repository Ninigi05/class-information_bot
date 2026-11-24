"""
Gmail トークンの保存（暗号化JSON）

旧形式は pickle（読み込むだけでコード実行されうる＋平文）だったため、
資格情報JSONを Fernet で暗号化して保存する形式に置き換える。
暗号鍵は環境変数 GMAIL_TOKEN_ENCRYPTION_KEY から読む（コードやデータとは分離）。
旧 pickle が残っている場合は、読み込み時に自動で新形式へ移行する。
"""

import json
import logging
import os
import pickle

from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)
BASE_DIR = os.getcwd()
TOKEN_DIR = os.path.join(BASE_DIR, "gmail_tokens")
ENV_KEY_NAME = "GMAIL_TOKEN_ENCRYPTION_KEY"


def _fernet():
    key = (os.getenv(ENV_KEY_NAME) or "").strip()
    if not key:
        logger.warning(f"[Gmail] {ENV_KEY_NAME} が未設定のため、トークンの暗号化ができません")
        return None
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as e:
        logger.warning(f"[Gmail] 暗号鍵の形式が不正です: {e}")
        return None


def json_path(user_id) -> str:
    return os.path.join(TOKEN_DIR, f"user_{user_id}.json")


def legacy_path(user_id) -> str:
    return os.path.join(TOKEN_DIR, f"user_{user_id}.pickle")


def credentials_exist(user_id) -> bool:
    return os.path.exists(json_path(user_id)) or os.path.exists(legacy_path(user_id))


def save_credentials(user_id, creds) -> bool:
    """資格情報を暗号化JSONで保存する（権限0600、原子的書き換え）。成功で True。"""
    f = _fernet()
    if f is None:
        return False
    os.makedirs(TOKEN_DIR, exist_ok=True)
    doc = {"version": 1, "ciphertext": f.encrypt(creds.to_json().encode("utf-8")).decode("ascii")}
    path = json_path(user_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return True


def _load_legacy(user_id):
    try:
        with open(legacy_path(user_id), "rb") as fh:
            return pickle.load(fh)
    except Exception as e:
        logger.warning(f"[Gmail] 旧トークンの読み込みに失敗 user={user_id}: {e}")
        return None


def load_credentials(user_id):
    """保存済みの資格情報を返す。旧 pickle があれば新形式へ移行してから返す。"""
    path = json_path(user_id)
    if os.path.exists(path):
        f = _fernet()
        if f is None:
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            info = json.loads(f.decrypt(doc["ciphertext"].encode("ascii")).decode("utf-8"))
            return Credentials.from_authorized_user_info(info)
        except Exception as e:
            logger.warning(f"[Gmail] トークンの復号に失敗 user={user_id}: {e}")
            return None
    if os.path.exists(legacy_path(user_id)):
        creds = _load_legacy(user_id)
        if creds is not None:
            if save_credentials(user_id, creds):
                try:
                    os.remove(legacy_path(user_id))
                except Exception:
                    pass
                logger.info(f"[Gmail] トークンを暗号化JSONへ移行しました user={user_id}")
            else:
                logger.warning(f"[Gmail] トークンの移行を保留（暗号鍵が未設定） user={user_id}")
        return creds
    return None


def delete_credentials(user_id) -> bool:
    """新旧両形式の保存トークンを削除する。何か消したら True。"""
    removed = False
    for path in (json_path(user_id), legacy_path(user_id), legacy_path(user_id) + ".bak"):
        if os.path.exists(path):
            try:
                os.remove(path)
                removed = True
            except Exception:
                pass
    return removed
