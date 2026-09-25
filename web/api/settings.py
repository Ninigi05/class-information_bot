"""
設定管理 API エンドポイント
"""

import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional

from utils import (
    load_user_data,
    save_user_data,
    PERIOD_TO_TIME,
    TERM_FIRST,
    TERM_SECOND,
    normalize_term_key,
)
from web.auth import TokenData, get_current_user
from web.schemas import (
    PeriodTimeUpdate,
    TermSettingsUpdate,
    SettingsShowResponse,
    SuccessResponse,
    NotifySettingsUpdate,
)
from web.api.notification import notify_user_change

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsShowResponse)
async def get_settings(current_user: TokenData = Depends(get_current_user)):
    """
    ログインユーザーの設定情報を取得（時限や学期設定）

    Returns:
        設定情報
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        
        # 時限開始時刻
        period_overrides = user_data.get("period_overrides", {}) or {}
        period_times = {}
        for p in sorted(PERIOD_TO_TIME.keys(), key=int):
            period_times[p] = period_overrides.get(p) or PERIOD_TO_TIME[p]
        
        # 学期設定
        term_starts = user_data.get("term_start_dates", {}) or {}
        term_ranges = user_data.get("term_ranges", {}) or {}
        class_counts = user_data.get("class_count_targets", {}) or {}
        
        term_settings = {}
        for term in [TERM_FIRST, TERM_SECOND]:
            r = term_ranges.get(term, {})
            term_settings[term] = {
                "start_date": r.get("start") or term_starts.get(term),
                "end_date": r.get("end"),
                "class_count": class_counts.get(term),
            }
        
        # 通知設定
        notify_settings = user_data.get("notify_settings", {})
        normal = notify_settings.get("normal", {})
        morning_time = user_data.get("morning_notice_time", "08:00")
        
        return SettingsShowResponse(
            period_times=period_times,
            term_settings=term_settings,
            notify={
                "normal_first": normal.get("first", 15),
                "normal_second": normal.get("second", 10),
                "morning_time": morning_time,
            }
        )
    except Exception as e:
        logger.exception(f"設定取得エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="設定の取得に失敗しました",
        )


@router.post("/period-time", response_model=SuccessResponse)
async def update_period_time(
    period_data: PeriodTimeUpdate,
    current_user: TokenData = Depends(get_current_user),
):
    """
    時限開始時刻を設定

    Args:
        period_data: 時限と開始時刻

    Returns:
        成功メッセージ
    """
    try:
        # 時刻形式の検証
        import re
        if not re.match(r"^(2[0-3]|[01]?\d):[0-5]\d$", period_data.time):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='時刻は「HH:MM」の形式で入力してください',
            )
        
        user_id = current_user.user_id
        data = load_user_data(user_id)
        
        # 変更前の値を取得
        old_time = data.get("period_overrides", {}).get(period_data.period) or PERIOD_TO_TIME.get(period_data.period, "未設定")
        
        data.setdefault("period_overrides", {})[period_data.period] = period_data.time
        save_user_data(user_id, data)
        
        logger.info(f"時限設定更新: user_id={user_id} period={period_data.period} time={period_data.time}")
        
        # DM通知を送信
        msg = (
            f"時限の開始時刻設定を更新しました。\n\n"
            f"・対象時限: {period_data.period}限\n"
            f"・開始時刻: {old_time} ➔ {period_data.time}"
        )
        await notify_user_change(user_id, msg)
        
        return SuccessResponse(message=f"{period_data.period}限の開始時刻を {period_data.time} に設定しました", data=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"時限設定エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="時限設定の更新に失敗しました",
        )


@router.put("", response_model=SuccessResponse)
async def update_notify_settings(
    notify_data: NotifySettingsUpdate,
    current_user: TokenData = Depends(get_current_user),
):
    """
    通知設定（タイミング）を更新

    Args:
        notify_data: 通知設定情報

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        data = load_user_data(user_id)
        
        # 変更前の設定を取得
        old_notify = data.get("notify_settings", {}).get("normal", {"first": 15, "second": 10}).copy()
        old_morning = data.get("morning_notice_time", "08:00")
        
        if notify_data.notify:
            n = notify_data.notify
            user_notify = data.setdefault("notify_settings", {})
            
            # 通常通知
            normal = user_notify.setdefault("normal", {"first": 15, "second": 10})
            if n.normal_first is not None:
                normal["first"] = n.normal_first
            if n.normal_second is not None:
                normal["second"] = n.normal_second
            
            # 朝の通知時刻
            if n.morning_time:
                import re
                if re.match(r"^(2[0-3]|[01]?\d):[0-5]\d$", n.morning_time):
                    data["morning_notice_time"] = n.morning_time
        
        save_user_data(user_id, data)
        logger.info(f"通知設定更新: user_id={user_id}")
        
        # DM通知を送信
        new_notify = data.get("notify_settings", {}).get("normal", {})
        new_morning = data.get("morning_notice_time", "08:00")
        msg = (
            f"通知タイミング設定を更新しました。\n\n"
            f"■ 変更後の設定:\n"
            f"・1回目の通常通知: {new_notify.get('first')}分前 (変更前: {old_notify.get('first')}分前)\n"
            f"・2回目の通常通知: {new_notify.get('second')}分前 (変更前: {old_notify.get('second')}分前)\n"
            f"・朝の通知時刻: {new_morning} (変更前: {old_morning})"
        )
        await notify_user_change(user_id, msg)
        
        return SuccessResponse(message="通知設定を更新しました", data=None)
    except Exception as e:
        logger.exception(f"通知設定更新エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="通知設定の更新に失敗しました",
        )


@router.post("/term", response_model=SuccessResponse)
async def update_term_settings(
    term_data: TermSettingsUpdate,
    current_user: TokenData = Depends(get_current_user),
):
    """
    学期設定（開始日・授業回数）を更新

    Args:
        term_data: 学期設定情報

    Returns:
        成功メッセージ
    """
    try:
        term = normalize_term_key(term_data.term)
        
        if term not in [TERM_FIRST, TERM_SECOND]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"無効な学期です: {term_data.term}",
            )
        
        user_id = current_user.user_id
        data = load_user_data(user_id)
        
        # 変更前のデータを保持
        old_starts = data.get("term_start_dates", {}).get(term, "未設定")
        old_ranges = data.get("term_ranges", {}).get(term, {})
        old_start = old_ranges.get("start") or old_starts
        old_end = old_ranges.get("end", "未設定")
        old_count = data.get("class_count_targets", {}).get(term, "未設定")
        
        # 期間設定（開始日・終了日）
        if term_data.start_date or term_data.end_date:
            r = data.setdefault("term_ranges", {}).setdefault(term, {})
            if term_data.start_date:
                try:
                    datetime.strptime(term_data.start_date, "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail='開始日は「YYYY-MM-DD」の形式で入力してください',
                    )
                r["start"] = term_data.start_date
                # 互換性のために古いキーも更新
                data.setdefault("term_start_dates", {})[term] = term_data.start_date
            
            if term_data.end_date:
                try:
                    datetime.strptime(term_data.end_date, "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail='終了日は「YYYY-MM-DD」の形式で入力してください',
                    )
                r["end"] = term_data.end_date
        
        # 授業回数設定
        if term_data.class_count:
            if term_data.class_count < 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="授業回数は1以上の整数で入力してください",
                )
            data.setdefault("class_count_targets", {})[term] = term_data.class_count
            
            # 授業回数変更時に出席カウントをリセット
            attendance = data.get("class_attendance_count", {}) or {}
            attendance[term] = {}
            data["class_attendance_count"] = attendance
        
        save_user_data(user_id, data)
        
        logger.info(f"学期設定更新: user_id={user_id} term={term} start_date={term_data.start_date} class_count={term_data.class_count}")
        
        # 変更後のデータ
        new_ranges = data.get("term_ranges", {}).get(term, {})
        new_start = new_ranges.get("start") or data.get("term_start_dates", {}).get(term, "未設定")
        new_end = new_ranges.get("end", "未設定")
        new_count = data.get("class_count_targets", {}).get(term, "未設定")
        
        # DM通知を送信
        msg = (
            f"学期設定（{term_data.term}）を更新しました。\n\n"
            f"■ 変更後の設定:\n"
            f"・開始日: {new_start} (変更前: {old_start})\n"
            f"・終了日: {new_end} (変更前: {old_end})\n"
            f"・目標授業回数: {new_count}回 (変更前: {old_count}回)"
        )
        await notify_user_change(user_id, msg)
        
        return SuccessResponse(message=f"{term}の設定を更新しました", data=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"学期設定エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="学期設定の更新に失敗しました",
        )
