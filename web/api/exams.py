"""
試験時間割管理 API エンドポイント

保存形式は Bot と同じ（exam_schedules_by_term[学期] = [{name, start, end, classes: [{day, period, ...}]}]）。
"""

import logging
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status

from utils import (
    load_user_data,
    save_user_data,
    WEEKDAYS,
    WEEKDAY_MAP,
    PERIOD_TO_TIME,
    TERM_FIRST,
    TERM_SECOND,
    normalize_term_key,
    populate_exam_schedule,
    add_exam_class,
    remove_exam_class,
)
from web.auth import TokenData, get_current_user
from web.schemas import (
    ExamScheduleCreate,
    ExamScheduleUpdate,
    ExamClassAddRequest,
    ListResponse,
    SuccessResponse,
)
from web.api.notification import notify_user_change

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/exams", tags=["exams"])
TIME_RE = re.compile(r"^(2[0-3]|[01]?\d):[0-5]\d$")


def _term(term: str | None) -> str:
    selected = normalize_term_key(term)
    if selected not in (TERM_FIRST, TERM_SECOND):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"無効な学期です: {term}"
        )
    return selected


def _check_dates(start: str, end: str) -> None:
    try:
        sd = datetime.strptime(start or "", "%Y-%m-%d").date()
        ed = datetime.strptime(end or "", "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="日付は「YYYY-MM-DD」の形式で入力してください",
        )
    if sd > ed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="開始日は終了日より前にしてください",
        )


def _find(schedules: list, name: str) -> dict:
    target = next((s for s in schedules if s.get("name") == name), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="試験時間割が見つかりません"
        )
    return target


def _for_response(sched: dict) -> dict:
    classes = []
    for c in sched.get("classes", []) or []:
        try:
            weekday = WEEKDAYS[int(c.get("day"))]
        except (TypeError, ValueError, IndexError):
            continue
        classes.append({**c, "weekday": weekday})
    classes.sort(key=lambda c: (int(c["day"]), str(c.get("period", ""))))
    return {
        "name": sched.get("name"),
        "start": sched.get("start"),
        "end": sched.get("end"),
        "classes": classes,
    }


@router.get("", response_model=ListResponse)
async def get_exam_schedules(
    term: str = None, current_user: TokenData = Depends(get_current_user)
):
    """試験時間割一覧（授業を含む）を取得"""
    selected_term = _term(term)
    user_data = load_user_data(current_user.user_id)
    schedules = user_data.get("exam_schedules_by_term", {}).get(selected_term, []) or []
    items = [_for_response(s) for s in sorted(schedules, key=lambda s: s.get("start", ""))]
    return ListResponse(count=len(items), items=items)


@router.post("", response_model=SuccessResponse)
async def create_exam_schedule(
    schedule_data: ExamScheduleCreate,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """試験時間割を作成し、定期試験ありの授業を自動で追加する"""
    selected_term = _term(term)
    name = schedule_data.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="時間割名を入力してください"
        )
    _check_dates(schedule_data.start_date, schedule_data.end_date)

    user_id = current_user.user_id
    user_data = load_user_data(user_id)
    schedules = user_data.setdefault("exam_schedules_by_term", {}).setdefault(
        selected_term, []
    )
    if any(s.get("name") == name for s in schedules):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="同名の試験時間割が既に存在します"
        )
    sched = {
        "name": name,
        "start": schedule_data.start_date,
        "end": schedule_data.end_date,
        "classes": [],
    }
    added = populate_exam_schedule(user_data, selected_term, sched)
    schedules.append(sched)
    save_user_data(user_id, user_data)

    await notify_user_change(
        user_id,
        f"試験時間割「{name}」を作成しました。\n\n"
        f"・学期: {selected_term}\n"
        f"・期間: {sched['start']} 〜 {sched['end']}\n"
        f"・定期試験ありの授業 {added}件を自動で追加しました",
    )
    return SuccessResponse(message="試験時間割を作成しました", data=_for_response(sched))


@router.put("/{schedule_name}", response_model=SuccessResponse)
async def update_exam_schedule(
    schedule_name: str,
    update_data: ExamScheduleUpdate,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """試験時間割の名前・期間を変更"""
    selected_term = _term(term)
    user_id = current_user.user_id
    user_data = load_user_data(user_id)
    schedules = user_data.get("exam_schedules_by_term", {}).get(selected_term, []) or []
    target = _find(schedules, schedule_name)

    new_name = (update_data.name or "").strip() or target.get("name")
    if new_name != schedule_name and any(s.get("name") == new_name for s in schedules):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="同名の試験時間割が既に存在します"
        )
    new_start = update_data.start_date or target.get("start")
    new_end = update_data.end_date or target.get("end")
    _check_dates(new_start, new_end)

    old = f"{schedule_name}（{target.get('start')} 〜 {target.get('end')}）"
    target.update({"name": new_name, "start": new_start, "end": new_end})
    save_user_data(user_id, user_data)
    await notify_user_change(
        user_id,
        f"試験時間割を変更しました。\n\n・{old}\n→ {new_name}（{new_start} 〜 {new_end}）",
    )
    return SuccessResponse(message="試験時間割を変更しました", data=_for_response(target))


@router.delete("/{schedule_name}", response_model=SuccessResponse)
async def delete_exam_schedule(
    schedule_name: str,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """試験時間割を削除"""
    selected_term = _term(term)
    user_id = current_user.user_id
    user_data = load_user_data(user_id)
    schedules = user_data.get("exam_schedules_by_term", {}).get(selected_term, []) or []
    target = _find(schedules, schedule_name)
    schedules.remove(target)
    save_user_data(user_id, user_data)
    await notify_user_change(
        user_id, f"試験時間割「{schedule_name}」（{selected_term}）を削除しました。"
    )
    return SuccessResponse(message="試験時間割を削除しました")


@router.post("/{schedule_name}/classes", response_model=SuccessResponse)
async def add_class_to_exam(
    schedule_name: str,
    class_data: ExamClassAddRequest,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """試験時間割に授業を追加（同じ曜日・時限の既存分は置き換える）"""
    selected_term = _term(term)
    if class_data.weekday not in WEEKDAY_MAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"無効な曜日です: {class_data.weekday}",
        )
    if class_data.period not in PERIOD_TO_TIME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"無効な時限です: {class_data.period}",
        )
    if class_data.time and not TIME_RE.match(class_data.time):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="時刻は「HH:MM」の形式で入力してください",
        )

    user_id = current_user.user_id
    user_data = load_user_data(user_id)
    schedules = user_data.get("exam_schedules_by_term", {}).get(selected_term, []) or []
    target = _find(schedules, schedule_name)
    entry = {
        "day": WEEKDAY_MAP[class_data.weekday],
        "period": class_data.period,
        "subject": class_data.subject,
        "room": class_data.room,
    }
    if class_data.time:
        entry["time"] = class_data.time
    add_exam_class(target, entry)
    save_user_data(user_id, user_data)
    await notify_user_change(
        user_id,
        f"試験時間割「{schedule_name}」に授業を追加しました。\n\n"
        f"・{class_data.weekday} {class_data.period}限 {class_data.subject}（{class_data.room}）",
    )
    return SuccessResponse(message="試験時間割に授業を追加しました", data=_for_response(target))


@router.delete("/{schedule_name}/classes/{weekday}/{period}", response_model=SuccessResponse)
async def remove_class_from_exam(
    schedule_name: str,
    weekday: str,
    period: str,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """試験時間割から授業を削除（自動追加分は、時間割を編集しても再追加しない）"""
    selected_term = _term(term)
    if weekday not in WEEKDAY_MAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"無効な曜日です: {weekday}"
        )
    user_id = current_user.user_id
    user_data = load_user_data(user_id)
    schedules = user_data.get("exam_schedules_by_term", {}).get(selected_term, []) or []
    target = _find(schedules, schedule_name)
    if not remove_exam_class(target, WEEKDAY_MAP[weekday], period):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="授業が見つかりません"
        )
    save_user_data(user_id, user_data)
    await notify_user_change(
        user_id,
        f"試験時間割「{schedule_name}」から {weekday} {period}限を削除しました。",
    )
    return SuccessResponse(message="試験時間割から授業を削除しました", data=_for_response(target))
