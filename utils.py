import json
import os
import logging

BASE_DIR = os.getcwd()
logger = logging.getLogger(__name__)

WEEKDAYS = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]
WEEKDAY_MAP = {w: i for i, w in enumerate(WEEKDAYS)}

PERIOD_TO_TIME = {
    "1": "09:00",
    "2": "10:45",
    "3": "13:15",
    "4": "15:00",
    "5": "16:45",
    "6": "18:25",
}

DEFAULT_NOTIFY = {
    "normal": {"first": 15, "second": 10},
    "exam": {"first": 30, "second": 25},
}

# Use current month to determine academic term (1st: April-September, 2nd: October-March)
from datetime import datetime


def get_current_term() -> str:
    """Return "1st" or "2nd" based on current month."""
    month = datetime.now().month
    return "1st" if 4 <= month <= 9 else "2nd"


def load_user_data(user_id):
    path = os.path.join(BASE_DIR, f"user_{user_id}.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Backward compatibility: migrate old single-term structure to new structure
    data = _migrate_user_data_to_term_aware(data)
    return data


def _migrate_user_data_to_term_aware(data: dict) -> dict:
    """Migrate legacy single-term data structure to term-aware structure."""
    # Migrate classes
    if "classes" in data and not isinstance(data["classes"], dict):
        classes_list = data.pop("classes", [])
        if "classes_by_term" not in data:
            data["classes_by_term"] = {"1st": classes_list, "2nd": []}
    elif "classes_by_term" not in data:
        data["classes_by_term"] = {"1st": [], "2nd": []}

    # Migrate exam_schedules
    if "exam_schedules" in data and not isinstance(data["exam_schedules"], dict):
        exam_list = data.pop("exam_schedules", [])
        if "exam_schedules_by_term" not in data:
            data["exam_schedules_by_term"] = {"1st": exam_list, "2nd": []}
    elif "exam_schedules_by_term" not in data:
        data["exam_schedules_by_term"] = {"1st": [], "2nd": []}

    return data


def save_user_data(user_id, data):
    path = os.path.join(BASE_DIR, f"user_{user_id}.json")
    # Clean up legacy keys before saving
    data_to_save = dict(data)
    data_to_save.pop("classes", None)  # Remove old single-term keys
    data_to_save.pop("exam_schedules", None)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data_to_save, f, indent=4, ensure_ascii=False)


async def send_dm(user, message):
    try:
        dm = await user.create_dm()
        await dm.send(message)
    except Exception as e:
        try:
            uid = user.id
        except Exception:
            uid = "unknown"
        logger.error(f"DM送信失敗: {uid} ({e})")


async def send_long_dm(user, text, chunk_size=1900):
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    try:
        dm = await user.create_dm()
        for chunk in chunks:
            await dm.send(chunk)
    except Exception as e:
        try:
            uid = user.id
        except Exception:
            uid = "unknown"
        logger.error(f"DM送信エラー: {uid} ({e})")
