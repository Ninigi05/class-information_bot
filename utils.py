import json
import os
import logging
import fcntl
from datetime import datetime

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

TERM_FIRST = "前期"
TERM_SECOND = "後期"
TERM_ALIASES = {
    "1st": TERM_FIRST,
    "first": TERM_FIRST,
    "前期": TERM_FIRST,
    "2nd": TERM_SECOND,
    "second": TERM_SECOND,
    "後期": TERM_SECOND,
}


def get_current_term_orig() -> str:
    """Return "前期" or "後期" based on current month."""
    month = datetime.now().month
    return TERM_FIRST if 4 <= month <= 9 else TERM_SECOND


def get_current_term(user_id: int | None = None) -> str:
    if user_id is not None:
        return get_effective_term(user_id)
    return get_current_term_orig()


def normalize_term_key(term: str | None) -> str:
    """Normalize term labels to canonical Japanese keys."""
    key = str(term or "").strip().lower()
    if not key:
        return get_current_term_orig()
    return TERM_ALIASES.get(key, str(term).strip())


def get_effective_term(user_id: int) -> str:
    """
    ユーザー設定�E開始日・終亁E��を老E�Eして、現在の学期を判定する、E
    設定がなぁE��合�E従来の get_current_term() (月判宁E にフォールバックする、E
    """
    from utils import load_user_data, get_current_term
    from datetime import datetime

    data = load_user_data(user_id)
    term_ranges = data.get("term_ranges", {}) or {}
    now_date = datetime.now().strftime("%Y-%m-%d")

    for term in ["前期", "後期"]:
        term_range = term_ranges.get(term, {})
        start = term_range.get("start")
        end = term_range.get("end")
        if start and end:
            if start <= now_date <= end:
                logger.info(
                    f"[Term Detection] User={user_id} detected as '{term}' (Setting-based: {start} to {end})"
                )
                return term

    term = get_current_term()
    logger.info(
        f"[Term Detection] User={user_id} detected as '{term}' (Month-based fallback)"
    )
    return term


def get_attendance_key(term: str, weekday: int, period: str, subject: str) -> str:
    """Generate a unique key for tracking class attendance."""
    return f"{term}|{weekday}|{period}|{subject}"


def get_user_data_mtime(user_id):
    """ユーザーチE�Eタの最終更新時刻を取得すめE"""
    path = os.path.join(BASE_DIR, f"user_{user_id}.json")
    if not os.path.exists(path):
        return 0
    return os.path.getmtime(path)


def load_user_data(user_id):
    path = os.path.join(BASE_DIR, f"user_{user_id}.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            # 読み込み時も共有ロチE��を取得して読み込み中の書き込みを防ぁE
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            data = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"チE�Eタ読み込み失敁Euser={user_id}: {e}")
        return {}

    # Backward compatibility: migrate old single-term structure to new structure
    data = _migrate_user_data_to_term_aware(data)
    return data


def _migrate_user_data_to_term_aware(data: dict) -> dict:
    "Migrate legacy single-term data structure to term-aware structure."

    def _normalize_term_map(raw: dict | None) -> dict:
        normalized: dict[str, list] = {TERM_FIRST: [], TERM_SECOND: []}
        if not isinstance(raw, dict):
            return normalized

        for k, v in raw.items():
            term_key = normalize_term_key(k)
            if term_key not in normalized:
                continue
            if isinstance(v, list):
                normalized[term_key].extend(v)
        return normalized

    # Migrate classes
    if "classes" in data and not isinstance(data["classes"], dict):
        classes_list = data.pop("classes", [])
        if "classes_by_term" not in data:
            data["classes_by_term"] = {TERM_FIRST: classes_list, TERM_SECOND: []}
    data["classes_by_term"] = _normalize_term_map(data.get("classes_by_term"))

    # Migrate exam_schedules
    if "exam_schedules" in data and not isinstance(data["exam_schedules"], dict):
        exam_list = data.pop("exam_schedules", [])
        if "exam_schedules_by_term" not in data:
            data["exam_schedules_by_term"] = {
                TERM_FIRST: exam_list,
                TERM_SECOND: [],
            }
    data["exam_schedules_by_term"] = _normalize_term_map(
        data.get("exam_schedules_by_term")
    )

    return data


def save_user_data(user_id, data):
    path = os.path.join(BASE_DIR, f"user_{user_id}.json")
    # Clean up legacy keys before saving
    data_to_save = dict(data)
    data_to_save.pop("classes", None)  # Remove old single-term keys
    data_to_save.pop("exam_schedules", None)

    # Write atomically: write to temp file then replace
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            # 排他ロチE��を取得して書き込み
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(data_to_save, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        logger.exception(f"ユーザーチE�Eタ保存失敁E {path}")


async def send_dm(user, message):
    try:
        dm = await user.create_dm()
        await dm.send(message)
    except Exception as e:
        try:
            uid = user.id
        except Exception:
            uid = "unknown"
        logger.error(f"DM送信失敁E {uid} ({e})")


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
