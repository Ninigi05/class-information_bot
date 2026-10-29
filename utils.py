import json
import os
import logging
try:
    import fcntl
except ImportError:
    fcntl = None
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
    ユーザー設定の開始日・終了日を考慮して、現在の学期を判定する。
    毎日朝8:00のみ判定を行い、その日のキャッシュを保持する。
    設定がない場合は従来の get_current_term() (月判定) にフォールバックする。
    """
    from utils import load_user_data, save_user_data, get_current_term
    from datetime import datetime, timedelta

    data = load_user_data(user_id)
    cached_term_info = data.get("effective_term_cache", {})
    now_dt = datetime.now()
    now_date_str = now_dt.strftime("%Y-%m-%d")
    
    last_checked_date_str = cached_term_info.get("date")
    cached_term = cached_term_info.get("term")

    # 毎日朝8:00を境に判定を更新する。
    # 現在時刻が8:00未満の場合、今日の朝8:00にはまだ達していないため、判定基準日は「前日」とする。
    # 8:00以降の場合は判定基準日は「今日」とする。
    if now_dt.hour < 8:
        effective_base_date = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        effective_base_date = now_date_str

    if cached_term and last_checked_date_str == effective_base_date:
        return cached_term

    # 毎日朝8:00の判定タイミング、またはキャッシュがない場合：学期判定を実行
    term_ranges = data.get("term_ranges", {}) or {}
    determined_term = None

    for term in ["前期", "後期"]:
        term_range = term_ranges.get(term, {})
        start = term_range.get("start")
        end = term_range.get("end")
        if start and end:
            if start <= now_date_str <= end:
                determined_term = term
                logger.info(
                    f"[Term Detection (Daily 8:00)] User={user_id} detected as '{term}' (Setting-based: {start} to {end})"
                )
                break

    if not determined_term:
        determined_term = get_current_term()
        logger.info(
            f"[Term Detection (Daily 8:00)] User={user_id} detected as '{determined_term}' (Month-based fallback)"
        )

    # キャッシュを更新して保存
    data["effective_term_cache"] = {
        "term": determined_term,
        "date": effective_base_date
    }
    save_user_data(user_id, data)

    return determined_term



def get_term_for_date(data: dict, date_str: str) -> str:
    """指定日が属する学期を返す（term_ranges 優先、無ければ月で判定）。"""
    term_ranges = data.get("term_ranges", {}) or {}
    for term in (TERM_FIRST, TERM_SECOND):
        r = term_ranges.get(term) or {}
        if r.get("start") and r.get("end") and r["start"] <= date_str <= r["end"]:
            return term
    month = datetime.strptime(date_str, "%Y-%m-%d").month
    return TERM_FIRST if 4 <= month <= 9 else TERM_SECOND


def get_regular_classes_on_date(data: dict, term: str, date_str: str) -> list[dict]:
    """指定日に実施される通常授業（曜日上書きを考慮、休講は除外しない）を返す。"""
    weekday = datetime.strptime(date_str, "%Y-%m-%d").weekday()
    result = []
    for cls in data.get("classes_by_term", {}).get(term, []) or []:
        day = (cls.get("overrides") or {}).get(date_str, cls.get("day"))
        try:
            if int(day) == weekday:
                result.append(cls)
        except (TypeError, ValueError):
            continue
    return result


def get_room_for_date(data: dict, cls: dict, date_str: str) -> str:
    """指定日の教室を返す（/class setroom の授業別設定 → Webの教室上書き → 通常の教室）。"""
    room_overrides = cls.get("room_overrides") or {}
    if room_overrides.get(date_str):
        return room_overrides[date_str]
    for r in data.get("room_overrides", []) or []:
        if (
            isinstance(r, dict)
            and r.get("date") == date_str
            and str(r.get("period")) == str(cls.get("period"))
            and r.get("room")
        ):
            return r["room"]
    return cls.get("room") or "未設定"


def is_class_cancelled(data: dict, date_str: str, period) -> bool:
    """指定日・時限の通常授業が休講登録されているか。"""
    return any(
        c.get("date") == date_str and str(c.get("period")) == str(period)
        for c in data.get("cancelled_classes", []) or []
    )


def get_makeups_on_date(data: dict, date_str: str) -> list[dict]:
    """指定日の補講を、通知処理で通常授業と同様に扱える形のコピーで返す。"""
    result = []
    for m in data.get("makeup_classes", []) or []:
        if m.get("date") != date_str:
            continue
        item = dict(m)
        item["_is_makeup"] = True
        result.append(item)
    return result


def _validate_date_period(date_str: str, period) -> str:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise ValueError("日付形式が無効です。YYYY-MM-DD で指定してください")
    period = str(period).strip()
    if period not in PERIOD_TO_TIME:
        raise ValueError(f"時限が無効です（{period}）")
    return period


def add_makeup_class(
    data: dict,
    date_str: str,
    period,
    source_day: int,
    source_period,
    room: str | None = None,
    term: str | None = None,
) -> dict:
    """既存の授業（source_day, source_period）を指定日・時限に補講として追加する。

    入力不正時は ValueError を送出する。追加したレコードを返す。
    """
    period = _validate_date_period(date_str, period)
    source_period = str(source_period).strip()
    term = normalize_term_key(term) if term else get_term_for_date(data, date_str)

    source = next(
        (
            c
            for c in data.get("classes_by_term", {}).get(term, []) or []
            if str(c.get("day")) == str(source_day)
            and str(c.get("period")) == source_period
        ),
        None,
    )
    if source is None:
        raise ValueError(f"{term}に元となる授業が登録されていません")

    makeups = data.setdefault("makeup_classes", [])
    # 同じ日・時限でも、元の授業が異なれば複数登録できる
    if any(
        m.get("date") == date_str
        and str(m.get("period")) == period
        and str(m.get("day")) == str(source_day)
        and str(m.get("source_period")) == source_period
        for m in makeups
    ):
        raise ValueError(
            f"{date_str} {period}限には既に同じ授業の補講が登録されています"
        )

    record = {
        "date": date_str,
        "period": period,
        "subject": source.get("subject"),
        "room": (room or "").strip() or source.get("room", "未設定"),
        "day": int(source_day),
        "source_period": source_period,
        "term": term,
    }
    makeups.append(record)
    makeups.sort(key=lambda m: (m.get("date", ""), str(m.get("period", ""))))
    return record


def remove_makeup_class(
    data: dict, date_str: str, period, source_day=None, source_period=None
) -> dict | None:
    """補講を削除する。同じ日・時限に複数ある場合は元の授業（source_*）の指定が必要。

    該当なしは None、元の授業の指定がなく候補が複数ある場合は ValueError。
    """
    makeups = data.get("makeup_classes", []) or []
    matched = [
        i
        for i, m in enumerate(makeups)
        if m.get("date") == date_str
        and str(m.get("period")) == str(period)
        and (source_day is None or str(m.get("day")) == str(source_day))
        and (
            source_period is None
            or str(m.get("source_period")) == str(source_period)
        )
    ]
    if not matched:
        return None
    if len(matched) > 1:
        raise ValueError(
            f"{date_str} {period}限には補講が複数あります。削除する授業を指定してください"
        )
    return makeups.pop(matched[0])


def add_cancelled_class(
    data: dict, date_str: str, period, term: str | None = None
) -> dict:
    """指定日・時限の通常授業を休講にする。入力不正時は ValueError を送出する。"""
    period = _validate_date_period(date_str, period)
    term = normalize_term_key(term) if term else get_term_for_date(data, date_str)

    target = next(
        (
            c
            for c in get_regular_classes_on_date(data, term, date_str)
            if str(c.get("period")) == period
        ),
        None,
    )
    if target is None:
        raise ValueError(f"{date_str} {period}限に授業が登録されていません")
    if is_class_cancelled(data, date_str, period):
        raise ValueError(f"{date_str} {period}限は既に休講登録されています")

    record = {
        "date": date_str,
        "period": period,
        "subject": target.get("subject"),
        "term": term,
    }
    cancels = data.setdefault("cancelled_classes", [])
    cancels.append(record)
    cancels.sort(key=lambda c: (c.get("date", ""), str(c.get("period", ""))))
    return record


def remove_cancelled_class(data: dict, date_str: str, period) -> dict | None:
    cancels = data.get("cancelled_classes", []) or []
    for i, c in enumerate(cancels):
        if c.get("date") == date_str and str(c.get("period")) == str(period):
            return cancels.pop(i)
    return None


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
