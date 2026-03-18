"""
試験時間割管理 API エンドポイント
"""

import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from utils import load_user_data, save_user_data, WEEKDAYS, WEEKDAY_MAP, PERIOD_TO_TIME
from web.auth import TokenData, get_current_user
from web.schemas import (
    ExamScheduleCreate,
    ExamScheduleResponse,
    ExamClassAddRequest,
    ExamClassRemoveRequest,
    ListResponse,
    SuccessResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/exams", tags=["exams"])


@router.get("", response_model=ListResponse)
async def get_exam_schedules(current_user: TokenData = Depends(get_current_user)):
    """
    試験時間割一覧を取得

    Returns:
        試験時間割リスト
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        schedules = user_data.get("exam_schedules", [])

        return ListResponse(
            count=len(schedules),
            items=schedules,
        )
    except Exception as e:
        logger.exception(f"試験時間割一覧取得エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="試験時間割一覧の取得に失敗しました",
        )


@router.post("", response_model=SuccessResponse)
async def create_exam_schedule(
    schedule_data: ExamScheduleCreate,
    current_user: TokenData = Depends(get_current_user),
):
    """
    新しい試験時間割を作成

    Args:
        schedule_data: 試験時間割情報

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        schedules = user_data.get("exam_schedules", [])

        # 同じ名前の時間割が既に存在するかチェック
        for sched in schedules:
            if sched.get("name") == schedule_data.name:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="同じ名前の試験時間割が既に存在します",
                )

        # 新しい時間割を作成
        new_schedule = {
            "name": schedule_data.name,
            "start_date": schedule_data.start_date,
            "end_date": schedule_data.end_date,
            "classes": schedule_data.classes or [],
        }
        schedules.append(new_schedule)
        user_data["exam_schedules"] = schedules

        save_user_data(user_id, user_data)

        return SuccessResponse(
            message="試験時間割を作成しました",
            data={"schedule": new_schedule},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"試験時間割作成エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="試験時間割の作成に失敗しました",
        )


@router.get("/{schedule_name}", response_model=ExamScheduleResponse)
async def get_exam_schedule(
    schedule_name: str,
    current_user: TokenData = Depends(get_current_user),
):
    """
    試験時間割の詳細を取得

    Args:
        schedule_name: 時間割名

    Returns:
        試験時間割詳細
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        schedules = user_data.get("exam_schedules", [])

        target_schedule = None
        for sched in schedules:
            if sched.get("name") == schedule_name:
                target_schedule = sched
                break

        if not target_schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="試験時間割が見つかりません",
            )

        return ExamScheduleResponse(
            name=target_schedule.get("name"),
            start_date=target_schedule.get("start_date"),
            end_date=target_schedule.get("end_date"),
            classes=target_schedule.get("classes", []),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"試験時間割取得エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="試験時間割の取得に失敗しました",
        )


@router.post("/{schedule_name}/classes", response_model=SuccessResponse)
async def add_class_to_exam(
    schedule_name: str,
    class_data: ExamClassAddRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """
    試験時間割に授業を追加

    Args:
        schedule_name: 時間割名
        class_data: 授業情報

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        schedules = user_data.get("exam_schedules", [])

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

        target_schedule = None
        for sched in schedules:
            if sched.get("name") == schedule_name:
                target_schedule = sched
                break

        if not target_schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="試験時間割が見つかりません",
            )

        # 同じ曜日・時限の授業が既に存在するかチェック
        classes = target_schedule.get("classes", [])
        for cls in classes:
            if (
                cls.get("weekday") == class_data.weekday
                and cls.get("period") == class_data.period
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="この曜日・時限には既に授業が登録されています",
                )

        # 新しい授業を追加
        new_class = {
            "weekday": class_data.weekday,
            "period": class_data.period,
            "subject": class_data.subject,
            "room": class_data.room,
        }
        if class_data.time:
            new_class["time"] = class_data.time

        classes.append(new_class)
        target_schedule["classes"] = classes

        save_user_data(user_id, user_data)

        return SuccessResponse(
            message="試験時間割に授業を追加しました",
            data={"class": new_class},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"試験授業追加エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="授業の追加に失敗しました",
        )


@router.delete("/{schedule_name}/classes", response_model=SuccessResponse)
async def remove_class_from_exam(
    schedule_name: str,
    class_data: ExamClassRemoveRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """
    試験時間割から授業を削除

    Args:
        schedule_name: 時間割名
        class_data: 削除する授業情報

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        schedules = user_data.get("exam_schedules", [])

        target_schedule = None
        for sched in schedules:
            if sched.get("name") == schedule_name:
                target_schedule = sched
                break

        if not target_schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="試験時間割が見つかりません",
            )

        # 授業を削除
        classes = target_schedule.get("classes", [])
        found = False
        for i, cls in enumerate(classes):
            if (
                cls.get("weekday") == class_data.weekday
                and cls.get("period") == class_data.period
            ):
                classes.pop(i)
                found = True
                break

        if not found:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="授業が見つかりません",
            )

        target_schedule["classes"] = classes
        save_user_data(user_id, user_data)

        return SuccessResponse(
            message="試験時間割から授業を削除しました",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"試験授業削除エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="授業の削除に失敗しました",
        )


@router.delete("/{schedule_name}", response_model=SuccessResponse)
async def delete_exam_schedule(
    schedule_name: str,
    current_user: TokenData = Depends(get_current_user),
):
    """
    試験時間割を削除

    Args:
        schedule_name: 時間割名

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        schedules = user_data.get("exam_schedules", [])

        found = False
        for i, sched in enumerate(schedules):
            if sched.get("name") == schedule_name:
                schedules.pop(i)
                found = True
                break

        if not found:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="試験時間割が見つかりません",
            )

        user_data["exam_schedules"] = schedules
        save_user_data(user_id, user_data)

        return SuccessResponse(
            message="試験時間割を削除しました",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"試験時間割削除エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="試験時間割の削除に失敗しました",
        )
