import copy
import json
import os
import logging
import tempfile
import threading
from datetime import datetime, timedelta

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


def get_effective_term(user_id: int, data: dict | None = None) -> str:
    """
    ユーザー設定の開始日・終了日を考慮して、現在の学期を判定する。
    毎日朝8:00のみ判定を行い、その日のキャッシュを保持する。
    設定がない場合は従来の get_current_term() (月判定) にフォールバックする。
    data を渡すと、ファイルを読み直さずにそのデータで判定する。
    """
    if data is None:
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


def _all_classes(data: dict):
    for classes in (data.get("classes_by_term") or {}).values():
        yield from classes or []


def _all_exam_classes(data: dict):
    for schedules in (data.get("exam_schedules_by_term") or {}).values():
        for sched in schedules or []:
            yield from sched.get("classes", []) or []


# ============ 曜日変更・教室変更 ============
# 曜日変更は data["day_overrides"]（[{date, weekday}]）、教室変更は data["room_overrides"]（[{date, period, room}]）に保存する。
# 以前の形式（各授業の overrides / room_overrides）も読み取り・削除の対象にする。


def get_weekday_override(data: dict, date_str: str) -> int | None:
    """指定日の曜日変更先（0=月〜6=日）を返す。なければ None。"""
    for d in data.get("day_overrides", []) or []:
        if isinstance(d, dict) and d.get("date") == date_str:
            wd = d.get("weekday")
            if wd in WEEKDAY_MAP:
                return WEEKDAY_MAP[wd]
            try:
                return int(wd)
            except (TypeError, ValueError):
                pass
    for cls in _all_classes(data):
        day = (cls.get("overrides") or {}).get(date_str)
        if day is not None:
            try:
                return int(day)
            except (TypeError, ValueError):
                continue
    return None


def get_weekday_for_date(data: dict, date_str: str) -> int:
    """曜日変更を反映した、指定日に使う時間割の曜日を返す。"""
    override = get_weekday_override(data, date_str)
    if override is not None:
        return override
    return datetime.strptime(date_str, "%Y-%m-%d").weekday()


def list_day_overrides(data: dict) -> list[dict]:
    """曜日変更の一覧（[{date, weekday}]、日付順）。"""
    dates = {
        d.get("date")
        for d in data.get("day_overrides", []) or []
        if isinstance(d, dict) and d.get("date")
    }
    for cls in _all_classes(data):
        dates.update((cls.get("overrides") or {}).keys())
    result = []
    for date_str in sorted(dates):
        wd = get_weekday_override(data, date_str)
        if wd is not None and 0 <= wd < len(WEEKDAYS):
            result.append({"date": date_str, "weekday": WEEKDAYS[wd]})
    return result


def set_day_override(data: dict, date_str: str, weekday: str) -> None:
    """指定日を別の曜日の時間割にする。入力不正時は ValueError。"""
    datetime.strptime(date_str, "%Y-%m-%d")
    if weekday not in WEEKDAY_MAP:
        raise ValueError(f"無効な曜日です（{weekday}）")
    remove_day_override(data, date_str)
    items = data.setdefault("day_overrides", [])
    items.append({"date": date_str, "weekday": weekday})
    items.sort(key=lambda d: d.get("date", ""))


def remove_day_override(data: dict, date_str: str) -> bool:
    """曜日変更を取り消す。取り消したら True。"""
    removed = False
    items = data.get("day_overrides", []) or []
    kept = [d for d in items if not (isinstance(d, dict) and d.get("date") == date_str)]
    if len(kept) != len(items):
        data["day_overrides"] = kept
        removed = True
    for cls in _all_classes(data):
        if date_str in (cls.get("overrides") or {}):
            del cls["overrides"][date_str]
            removed = True
    return removed


def replace_day_overrides(data: dict, entries: list) -> None:
    """曜日変更を entries の内容で置き換える（不正な項目は無視）。"""
    for d in list_day_overrides(data):
        remove_day_override(data, d["date"])
    for d in entries or []:
        if not isinstance(d, dict):
            continue
        try:
            set_day_override(
                data, str(d.get("date", "")).strip(), str(d.get("weekday", "")).strip()
            )
        except ValueError:
            continue


def list_room_overrides(data: dict) -> list[dict]:
    """教室変更の一覧（[{date, period, room}]、日付・時限順）。"""
    found: dict[tuple[str, str], str] = {}
    for cls in list(_all_classes(data)) + list(_all_exam_classes(data)):
        for date_str, room in (cls.get("room_overrides") or {}).items():
            if room:
                found.setdefault((date_str, str(cls.get("period"))), room)
    # 新しい形式の設定を優先する
    for r in data.get("room_overrides", []) or []:
        if isinstance(r, dict) and r.get("date") and r.get("room"):
            found[(r["date"], str(r.get("period")))] = r["room"]
    return [
        {"date": d, "period": p, "room": room}
        for (d, p), room in sorted(found.items())
    ]


def set_room_override(data: dict, date_str: str, period, room: str) -> None:
    """指定日・時限の教室を変更する。入力不正時は ValueError。"""
    period = _validate_date_period(date_str, period)
    room = (room or "").strip()
    if not room:
        raise ValueError("教室を入力してください")
    remove_room_override(data, date_str, period)
    items = data.setdefault("room_overrides", [])
    items.append({"date": date_str, "period": period, "room": room})
    items.sort(key=lambda r: (r.get("date", ""), str(r.get("period", ""))))


def remove_room_override(data: dict, date_str: str, period) -> bool:
    """教室変更を取り消す。取り消したら True。"""
    period = str(period)
    removed = False
    items = data.get("room_overrides", []) or []
    kept = [
        r
        for r in items
        if not (
            isinstance(r, dict)
            and r.get("date") == date_str
            and str(r.get("period")) == period
        )
    ]
    if len(kept) != len(items):
        data["room_overrides"] = kept
        removed = True
    for cls in list(_all_classes(data)) + list(_all_exam_classes(data)):
        if str(cls.get("period")) == period and date_str in (
            cls.get("room_overrides") or {}
        ):
            del cls["room_overrides"][date_str]
            removed = True
    return removed


def replace_room_overrides(data: dict, entries: list) -> None:
    """教室変更を entries の内容で置き換える（不正な項目は無視）。"""
    for r in list_room_overrides(data):
        remove_room_override(data, r["date"], r["period"])
    for r in entries or []:
        if not isinstance(r, dict):
            continue
        try:
            set_room_override(
                data,
                str(r.get("date", "")).strip(),
                str(r.get("period", "")).strip(),
                str(r.get("room", "")),
            )
        except ValueError:
            continue


def get_regular_classes_on_date(data: dict, term: str, date_str: str) -> list[dict]:
    """指定日に実施される通常授業（曜日上書きを考慮、休講は除外しない）を返す。"""
    weekday = get_weekday_for_date(data, date_str)
    result = []
    for cls in data.get("classes_by_term", {}).get(term, []) or []:
        try:
            if int(cls.get("day")) == weekday:
                result.append(cls)
        except (TypeError, ValueError):
            continue
    return result


def get_room_for_date(data: dict, cls: dict, date_str: str) -> str:
    """指定日の教室を返す（教室変更 → 以前の形式の授業別設定 → 通常の教室）。"""
    for r in data.get("room_overrides", []) or []:
        if (
            isinstance(r, dict)
            and r.get("date") == date_str
            and str(r.get("period")) == str(cls.get("period"))
            and r.get("room")
        ):
            return r["room"]
    room_overrides = cls.get("room_overrides") or {}
    if room_overrides.get(date_str):
        return room_overrides[date_str]
    return cls.get("room") or "未設定"


# ============ 定期試験の自動追加 ============
# 定期試験ありの授業（has_exam が False でない授業）は、その学期の試験時間割へ同じ曜日・時限で自動追加する。
# 自動追加した項目は from_class=True を持ち、手動で追加した項目とは区別する。
# 試験時間割から自動追加の項目を削除した場合は、その時間割の excluded（"曜日|時限"）に記録して再追加しない。


def class_has_exam(cls: dict) -> bool:
    return cls.get("has_exam", True) is not False


def _slot_key(day, period) -> str:
    return f"{day}|{period}"


def _sync_exam_entry(sched: dict, cls: dict) -> None:
    day, period = cls.get("day"), str(cls.get("period"))
    entries = sched.setdefault("classes", [])
    entries[:] = [
        e
        for e in entries
        if not (
            e.get("from_class")
            and str(e.get("day")) == str(day)
            and str(e.get("period")) == period
        )
    ]
    if not class_has_exam(cls):
        return
    if _slot_key(day, period) in (sched.get("excluded") or []):
        return
    if any(
        str(e.get("day")) == str(day) and str(e.get("period")) == period
        for e in entries
    ):
        return  # 手動で登録した試験を優先する
    entries.append(
        {
            "day": day,
            "period": period,
            "subject": cls.get("subject"),
            "room": cls.get("room"),
            "from_class": True,
        }
    )


def sync_exam_for_class(data: dict, term: str, cls: dict) -> None:
    """授業 cls の定期試験の有無を、その学期の全試験時間割に反映する。"""
    for sched in data.get("exam_schedules_by_term", {}).get(term, []) or []:
        _sync_exam_entry(sched, cls)


def remove_exam_for_class(data: dict, term: str, day, period) -> None:
    """削除した授業の、自動追加した試験を取り除く。"""
    sync_exam_for_class(
        data, term, {"day": day, "period": str(period), "has_exam": False}
    )


def sync_exams_after_classes_replaced(data: dict, term: str, old_classes: list) -> None:
    """時間割を丸ごと置き換えた後に、試験時間割の自動追加分を合わせる。"""
    new_slots = {
        _slot_key(c.get("day"), c.get("period"))
        for c in data.get("classes_by_term", {}).get(term, []) or []
    }
    for c in old_classes or []:
        if _slot_key(c.get("day"), c.get("period")) not in new_slots:
            remove_exam_for_class(data, term, c.get("day"), c.get("period"))
    for c in data.get("classes_by_term", {}).get(term, []) or []:
        sync_exam_for_class(data, term, c)


def populate_exam_schedule(data: dict, term: str, sched: dict) -> int:
    """新しく作った試験時間割に、定期試験ありの授業を追加する。追加件数を返す。"""
    before = len(sched.get("classes", []) or [])
    for c in data.get("classes_by_term", {}).get(term, []) or []:
        _sync_exam_entry(sched, c)
    return len(sched.get("classes", []) or []) - before


def remove_exam_class(sched: dict, day, period) -> int:
    """試験時間割から指定の曜日・時限を削除する。自動追加分は再追加しないよう記録する。"""
    entries = sched.get("classes", []) or []
    kept = [
        e
        for e in entries
        if not (str(e.get("day")) == str(day) and str(e.get("period")) == str(period))
    ]
    removed = [e for e in entries if e not in kept]
    if any(e.get("from_class") for e in removed):
        excluded = sched.setdefault("excluded", [])
        key = _slot_key(day, period)
        if key not in excluded:
            excluded.append(key)
    sched["classes"] = kept
    return len(removed)


def add_exam_class(sched: dict, entry: dict) -> None:
    """試験時間割に手動で授業を追加する（同じ曜日・時限の既存分は置き換える）。"""
    day, period = entry.get("day"), str(entry.get("period"))
    sched["classes"] = [
        e
        for e in sched.get("classes", []) or []
        if not (str(e.get("day")) == str(day) and str(e.get("period")) == period)
    ]
    sched["classes"].append(entry)
    key = _slot_key(day, period)
    if key in (sched.get("excluded") or []):
        sched["excluded"].remove(key)


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


# ユーザーデータの保存はユーザー単位のロックで直列化する。
# Bot（イベントループ）と Web API（uvicorn のスレッド）は同一プロセスで動くため、スレッド間のロックで足りる。
_user_locks: dict[str, threading.Lock] = {}
_user_locks_guard = threading.Lock()
_LEGACY_KEYS = ("classes", "exam_schedules")
_MISSING = object()


def _user_lock(user_id) -> threading.Lock:
    with _user_locks_guard:
        return _user_locks.setdefault(str(user_id), threading.Lock())


def _user_data_path(user_id) -> str:
    return os.path.join(BASE_DIR, f"user_{user_id}.json")


class UserData(dict):
    """load_user_data の戻り値。保存時のマージに使うため、読み込み時点の内容（_base）を保持する。"""

    __slots__ = ("_base",)


def get_user_data_mtime(user_id):
    """ユーザーデータの最終更新時刻を取得する"""
    path = _user_data_path(user_id)
    if not os.path.exists(path):
        return 0
    return os.path.getmtime(path)


def _read_user_file(user_id) -> dict | None:
    """ファイルを読んで移行済みの dict を返す。ファイルがなければ {}、壊れていれば None。"""
    path = _user_data_path(user_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"データ読み込み失敗 user={user_id}: {e}")
        return None
    # Backward compatibility: migrate old single-term structure to new structure
    return _migrate_user_data_to_term_aware(data)


def load_user_data(user_id):
    # Windows では読み込み中のファイルを os.replace で置き換えられないため、読み込みもロック内で行う
    with _user_lock(user_id):
        data = _read_user_file(user_id)
    result = UserData(data or {})
    result._base = copy.deepcopy(dict(result))
    return result


def _merge_value(base, ours, theirs):
    """3方向マージ。自分が変更した値を優先し、変更していない値は他者（ファイルの最新）の値を採る。"""
    if ours == base:
        return theirs
    if theirs == base or theirs == ours:
        return ours
    if isinstance(base, dict) and isinstance(ours, dict) and isinstance(theirs, dict):
        merged = {}
        for key in list(theirs) + [k for k in ours if k not in theirs]:
            value = _merge_value(
                base.get(key, _MISSING), ours.get(key, _MISSING), theirs.get(key, _MISSING)
            )
            if value is not _MISSING:
                merged[key] = value
        # 結果が自分の値と同じなら、呼び出し側が持つ参照を保つため元のオブジェクトを返す
        return ours if merged == ours else merged
    # 同じ値を双方が別々に変更した場合は、後から保存した側（自分）を優先する
    return ours


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
    """ユーザーデータを保存する。

    load_user_data で読んだデータなら、読み込み後に他の処理が保存した変更を消さないよう、
    保存直前のファイル内容と3方向マージしてから書き込む。マージ結果は data にも反映する。
    """
    path = _user_data_path(user_id)
    # Clean up legacy keys before saving
    ours = {k: v for k, v in data.items() if k not in _LEGACY_KEYS}
    base = getattr(data, "_base", None)

    try:
        with _user_lock(user_id):
            merged = ours
            if base is not None:
                theirs = _read_user_file(user_id)
                if theirs is not None:
                    merged = _merge_value(base, ours, theirs)

            # Write atomically: write to a unique temp file then replace
            fd, tmp_path = tempfile.mkstemp(
                dir=BASE_DIR, prefix=f".user_{user_id}.", suffix=".json.tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(merged, f, indent=4, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, path)
            except BaseException:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
    except Exception:
        logger.exception(f"ユーザーデータ保存失敗: {path}")
        return

    if isinstance(data, UserData):
        if merged is not ours:
            data.clear()
            data.update(merged)
        data._base = copy.deepcopy(dict(merged))


def delete_user_data(user_id) -> bool:
    """利用者のデータファイルを削除する。削除したら True。"""
    path = _user_data_path(user_id)
    with _user_lock(user_id):
        if not os.path.exists(path):
            return False
        os.remove(path)
    logger.info(f"ユーザーデータを削除しました: user={user_id}")
    return True


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
