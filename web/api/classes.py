"""
授業管理 API エンドポイント
"""

import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from utils import (
    load_user_data,
    save_user_data,
    WEEKDAYS,
    WEEKDAY_MAP,
    PERIOD_TO_TIME,
    get_current_term,
    normalize_term_key,
)
from web.auth import TokenData, get_current_user
from web.schemas import (
    ClassCreate,
    ClassResponse,
    ClassUpdate,
    ListResponse,
    SuccessResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/classes", tags=["classes"])


@router.get("", response_model=ListResponse)
async def get_classes(
    term: str = None, current_user: TokenData = Depends(get_current_user)
):
    """
    ログインユーザーの授業一覧を取得
    Returns:
        授業リスト
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        selected_term = normalize_term_key(term)
        classes = user_data.get("classes_by_term", {}).get(selected_term, [])

        # day フィールドを weekday に変換
        for cls in classes:
            if "day" in cls:
                cls["weekday"] = WEEKDAYS[int(cls["day"])]

        return ListResponse(
            count=len(classes),
            items=classes,
        )
    except Exception as e:
        logger.exception(f"授業一覧取得エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="授業一覧の取得に失敗しました",
        )


@router.post("", response_model=SuccessResponse)
async def add_class(
    class_data: ClassCreate,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """
    新しい授業を登録

    Args:
        class_data: 授業情報

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        selected_term = normalize_term_key(term)

        # weekday を day に変換
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

        # 同じ曜日・時限の授業が既に存在するかチェック
        user_data.setdefault("classes_by_term", {}).setdefault(selected_term, [])
        classes = user_data["classes_by_term"][selected_term]

        day = WEEKDAY_MAP[class_data.weekday]
        for cls in classes:
            if cls.get("day") == day and str(cls.get("period")) == str(
                class_data.period
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="この曜日・時限には既に授業が登録されています",
                )

        # 新しい授業を追加
        new_class = {
            "day": day,
            "period": class_data.period,
            "subject": class_data.subject,
            "room": class_data.room,
        }
        classes.append(new_class)

        # データを保存
        save_user_data(user_id, user_data)

        return SuccessResponse(
            message="授業を登録しました",
            data={"class": new_class},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"授業追加エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="授業の登録に失敗しました",
        )


@router.put("/{weekday}/{period}", response_model=SuccessResponse)
async def update_class(
    weekday: str,
    period: str,
    update_data: ClassUpdate,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """
    授業を更新

    Args:
        weekday: 曜日
        period: 時限
        update_data: 更新内容

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        selected_term = normalize_term_key(term)
        classes = user_data.get("classes_by_term", {}).get(selected_term, [])

        if weekday not in WEEKDAY_MAP:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"無効な曜日です: {weekday}",
            )

        day = WEEKDAY_MAP[weekday]

        # 対象の授業を探す
        target_class = None
        for cls in classes:
            if cls.get("day") == day and cls.get("period") == period:
                target_class = cls
                break

        if not target_class:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="授業が見つかりません",
            )

        # 更新
        if update_data.weekday is not None:
            if update_data.weekday not in WEEKDAY_MAP:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"無効な曜日です: {update_data.weekday}",
                )
            target_class["day"] = WEEKDAY_MAP[update_data.weekday]

        if update_data.period is not None:
            if update_data.period not in PERIOD_TO_TIME:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"無効な時限です: {update_data.period}",
                )
            target_class["period"] = update_data.period

        if update_data.subject is not None:
            target_class["subject"] = update_data.subject

        if update_data.room is not None:
            target_class["room"] = update_data.room

        # データを保存
        save_user_data(user_id, user_data)

        return SuccessResponse(
            message="授業を更新しました",
            data={"class": target_class},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"授業更新エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="授業の更新に失敗しました",
        )


@router.delete("/{weekday}/{period}", response_model=SuccessResponse)
async def delete_class(
    weekday: str,
    period: str,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """
    授業を削除

    Args:
        weekday: 曜日
        period: 時限

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        selected_term = normalize_term_key(term)
        classes = user_data.get("classes_by_term", {}).get(selected_term, [])

        if weekday not in WEEKDAY_MAP:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"無効な曜日です: {weekday}",
            )

        day = WEEKDAY_MAP[weekday]

        # 対象の授業を探して削除
        found = False
        for i, cls in enumerate(classes):
            if cls.get("day") == day and cls.get("period") == period:
                classes.pop(i)
                found = True
                break

        if not found:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="授業が見つかりません",
            )

        save_user_data(user_id, user_data)

        return SuccessResponse(
            message="授業を削除しました",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"授業削除エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="授業の削除に失敗しました",
        )


@router.get("/table", response_model=dict)
async def get_class_table(
    term: str = None, current_user: TokenData = Depends(get_current_user)
):
    """
    時間割テーブル形式でデータを取得（フロント用）

    Returns:
        曜日・時限をキーにした時間割テーブル
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        selected_term = normalize_term_key(term)
        classes = user_data.get("classes_by_term", {}).get(selected_term, [])

        # テーブル形式に変換：{weekday: {period: class_info}}
        table = {}
        for weekday_name in WEEKDAYS:
            table[weekday_name] = {}
            for period in PERIOD_TO_TIME.keys():
                table[weekday_name][period] = None

        # クラス情報をテーブルに配置
        for cls in classes:
            day = cls.get("day")
            period = cls.get("period")
            if day is not None and 0 <= day < len(WEEKDAYS):
                weekday_name = WEEKDAYS[day]
                table[weekday_name][period] = cls

        return table
    except Exception as e:
        logger.exception(f"時間割テーブル取得エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="時間割の取得に失敗しました",
        )
