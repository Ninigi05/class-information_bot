"""
利用者データの削除（/setting delete_data と Web ダッシュボードで共用）
"""

import json
import logging
import os

from mail_service import unlink_gmail
from utils import delete_user_data

logger = logging.getLogger(__name__)
BASE_DIR = os.getcwd()
MORNING_MARK_FILE = os.path.join(BASE_DIR, "morning_sent.json")


def _remove_morning_mark(user_id) -> None:
    try:
        with open(MORNING_MARK_FILE, "r", encoding="utf-8") as f:
            marks = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    if marks.pop(str(user_id), None) is not None:
        with open(MORNING_MARK_FILE, "w", encoding="utf-8") as f:
            json.dump(marks, f, ensure_ascii=False)


def delete_account(user_id) -> dict:
    """時間割・設定・Gmail 連携など、利用者に紐付くデータをすべて削除する。

    通信を伴う（Google のトークン取り消し）ため、イベントループからは別スレッドで呼ぶ。
    """
    gmail_removed = unlink_gmail(user_id)
    data_removed = delete_user_data(user_id)
    try:
        _remove_morning_mark(user_id)
    except Exception as e:
        logger.warning(f"[Account] 朝の通知記録の削除に失敗 user={user_id}: {e}")
    logger.info(
        f"[Account] データ削除 user={user_id} data={data_removed} gmail={gmail_removed}"
    )
    return {"data": data_removed, "gmail": gmail_removed}
