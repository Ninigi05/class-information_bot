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
)

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
        class_counts = user_data.get("class_count_targets", {}) or {}
        
        term_settings = {}
        for term in [TERM_FIRST, TERM_SECOND]:
            term_settings[term] = {
                "start_date": term_starts.get(term),
                "class_count": class_counts.get(term),
            }
        
        return SettingsShowResponse(
            period_times=period_times,
            term_settings=term_settings,
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
        data.setdefault("period_overrides", {})[period_data.period] = period_data.time
        save_user_data(user_id, data)
        
        logger.info(f"時限設定更新: user_id={user_id} period={period_data.period} time={period_data.time}")
        
        return SuccessResponse(message=f"{period_data.period}限の開始時刻を {period_data.time} に設定しました", data=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"時限設定エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="時限設定の更新に失敗しました",
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
        
        # 開始日設定
        if term_data.start_date:
            try:
                datetime.strptime(term_data.start_date, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='日付は「YYYY-MM-DD」の形式で入力してください',
                )
            data.setdefault("term_start_dates", {})[term] = term_data.start_date
        
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
        
        return SuccessResponse(message=f"{term}の設定を更新しました", data=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"学期設定エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="学期設定の更新に失敗しました",
        )
