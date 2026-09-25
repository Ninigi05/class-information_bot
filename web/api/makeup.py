"""
補講・休講管理 API エンドポイント
"""

import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from utils import (
    load_user_data,
    save_user_data,
    get_current_term,
    normalize_term_key,
)
from web.auth import TokenData, get_current_user
from web.schemas import (
    MakeupCreate,
    MakeupUpdate,
    CancelCreate,
    ListResponse,
    SuccessResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["makeup", "cancel"])
from web.api.notification import notify_user_change


# ============ 補講（Makeup）エンドポイント ============


@router.get("/api/makeup", response_model=ListResponse)
async def get_makeup_classes(
    term: str = None, current_user: TokenData = Depends(get_current_user)
):
    """
    補講一覧を取得

    Returns:
        補講リスト
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        selected_term = normalize_term_key(term)
        makeup_classes = user_data.get("makeup_classes_by_term", {}).get(
            selected_term, []
        )

        return ListResponse(
            count=len(makeup_classes),
            items=makeup_classes,
        )
    except Exception as e:
        logger.exception(f"補講一覧取得エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="取得失敗",
        )


@router.post("/api/makeup", response_model=SuccessResponse)
async def add_makeup_class(
    makeup_data: MakeupCreate,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """
    補講を追加

    Args:
        makeup_data: 補講情報

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        selected_term = normalize_term_key(term)
        user_data.setdefault("makeup_classes_by_term", {}).setdefault(selected_term, [])
        makeup_classes = user_data["makeup_classes_by_term"][selected_term]
        # 同じ日時の補講が存在するかチェック
        for mc in makeup_classes:
            if (
                mc.get("date") == makeup_data.date
                and mc.get("time") == makeup_data.time
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="既に存在します",
                )

        new_makeup = {
            "date": makeup_data.date,
            "time": makeup_data.time,
            "subject": makeup_data.subject,
            "room": makeup_data.room,
        }
        makeup_classes.append(new_makeup)
        save_user_data(user_id, user_data)

        # DM通知を送信
        msg = (
            f"時間割の上書き（補講）を登録しました。\n\n"
            f"・学期: {selected_term}\n"
            f"・日付: {makeup_data.date}\n"
            f"・時限/時刻: {makeup_data.time}\n"
            f"・科目名: {makeup_data.subject}\n"
            f"・教室: {makeup_data.room}"
        )
        await notify_user_change(user_id, msg)

        return SuccessResponse(
            message="追加しました",
            data={"makeup": new_makeup},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"追加エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="失敗しました",
        )


@router.delete("/api/makeup/{date}/{time}", response_model=SuccessResponse)
async def delete_makeup_class(
    date: str,
    time: str,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """
    補講を削除

    Args:
        date: 日付（YYYY-MM-DD）
        time: 時刻（HH:MM）
    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        selected_term = normalize_term_key(term)
        makeup_classes = user_data.get("makeup_classes_by_term", {}).get(
            selected_term, []
        )

        found = False
        deleted_makeup_info = None
        for i, mc in enumerate(makeup_classes):
            if mc.get("date") == date and mc.get("time") == time:
                deleted_makeup_info = makeup_classes.pop(i)
                found = True
                break

        if not found:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="補講が見つかりません",
            )

        user_data["makeup_classes_by_term"][selected_term] = makeup_classes
        save_user_data(user_id, user_data)

        # DM通知を送信
        msg = (
            f"時間割の上書き（補講）を削除しました。\n\n"
            f"・学期: {selected_term}\n"
            f"・日付: {date}\n"
            f"・時限/時刻: {time}\n"
            f"・科目名: {deleted_makeup_info.get('subject', '不明')}\n"
            f"・教室: {deleted_makeup_info.get('room', '不明')}"
        )
        await notify_user_change(user_id, msg)

        return SuccessResponse(
            message="補講を削除しました",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"補講削除エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="補講の削除に失敗しました",
        )


# ============ 手動休講（Cancel）エンドポイント ============


@router.get("/api/cancel", response_model=ListResponse)
async def get_cancel_classes(
    term: str = None, current_user: TokenData = Depends(get_current_user)
):
    """
    手動休講一覧を取得

    Returns:
        手動休講リスト
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        selected_term = normalize_term_key(term)
        cancel_classes = user_data.get("cancel_classes_by_term", {}).get(
            selected_term, []
        )

        return ListResponse(
            count=len(cancel_classes),
            items=cancel_classes,
        )
    except Exception as e:
        logger.exception(f"手動休講一覧取得エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="手動休講一覧の取得に失敗しました",
        )


@router.post("/api/cancel", response_model=SuccessResponse)
async def add_cancel_class(
    cancel_data: CancelCreate,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """
    手動休講を追加

    Args:
        cancel_data: 休講情報

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        selected_term = normalize_term_key(term)
        user_data.setdefault("cancel_classes_by_term", {}).setdefault(selected_term, [])
        cancel_classes = user_data["cancel_classes_by_term"][selected_term]

        # 同じ日付・授業の休講が存在するかチェック
        for cc in cancel_classes:
            if (
                cc.get("date") == cancel_data.date
                and cc.get("subject") == cancel_data.subject
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="この授業の休講は既に登録されています",
                )

        new_cancel = {
            "date": cancel_data.date,
            "subject": cancel_data.subject,
        }
        cancel_classes.append(new_cancel)
        save_user_data(user_id, user_data)

        # DM通知を送信
        msg = (
            f"時間割の上書き（休講）を登録しました。\n\n"
            f"・学期: {selected_term}\n"
            f"・日付: {cancel_data.date}\n"
            f"・科目名: {cancel_data.subject}"
        )
        await notify_user_change(user_id, msg)

        return SuccessResponse(
            message="休講を追加しました",
            data={"cancel": new_cancel},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"休講追加エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="休講の追加に失敗しました",
        )


@router.delete("/api/cancel/{date}/{subject}", response_model=SuccessResponse)
async def delete_cancel_class(
    date: str,
    subject: str,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """
    手動休講を削除

    Args:
        date: 日付（YYYY-MM-DD）
        subject: 授業名

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        selected_term = normalize_term_key(term)
        cancel_classes = user_data.get("cancel_classes_by_term", {}).get(
            selected_term, []
        )

        found = False
        deleted_cancel_info = None
        for i, cc in enumerate(cancel_classes):
            if cc.get("date") == date and cc.get("subject") == subject:
                deleted_cancel_info = cancel_classes.pop(i)
                found = True
                break

        if not found:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="休講が見つかりません",
            )

        user_data["cancel_classes_by_term"][selected_term] = cancel_classes
        save_user_data(user_id, user_data)

        # DM通知を送信
        msg = (
            f"時間割の上書き（休講）を削除しました。\n\n"
            f"・学期: {selected_term}\n"
            f"・日付: {date}\n"
            f"・科目名: {deleted_cancel_info.get('subject', '不明')}"
        )
        await notify_user_change(user_id, msg)

        return SuccessResponse(
            message="休講を削除しました",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"休講削除エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="休講の削除に失敗しました",
        )
