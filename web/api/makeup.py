"""
補講・休講管理 API エンドポイント
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status

from utils import (
    load_user_data,
    save_user_data,
    normalize_term_key,
    get_term_for_date,
    get_regular_classes_on_date,
    is_class_cancelled,
    add_makeup_class,
    remove_makeup_class,
    add_cancelled_class,
    remove_cancelled_class,
    TERM_FIRST,
    TERM_SECOND,
    WEEKDAYS,
    WEEKDAY_MAP,
    list_day_overrides,
    list_room_overrides,
    set_day_override,
    remove_day_override,
    set_room_override,
    remove_room_override,
    replace_day_overrides,
    replace_room_overrides,
)
from web.auth import TokenData, get_current_user
from web.schemas import (
    MakeupCreate,
    CancelCreate,
    ListResponse,
    SuccessResponse,
    OverridesBulkSaveRequest,
    DayOverrideCreate,
    RoomOverrideCreate,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["makeup", "cancel"])
from web.api.notification import notify_user_change


# ============ 補講（Makeup）エンドポイント ============


@router.get("/api/makeup", response_model=ListResponse)
async def get_makeup_classes(current_user: TokenData = Depends(get_current_user)):
    """
    補講一覧を取得

    Returns:
        補講リスト（日付・時限順）
    """
    try:
        user_data = load_user_data(current_user.user_id)
        makeup_classes = user_data.get("makeup_classes", []) or []
        return ListResponse(count=len(makeup_classes), items=makeup_classes)
    except Exception as e:
        logger.exception(f"補講一覧取得エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="補講一覧の取得に失敗しました",
        )


@router.post("/api/makeup", response_model=SuccessResponse)
async def add_makeup(
    makeup_data: MakeupCreate,
    current_user: TokenData = Depends(get_current_user),
):
    """
    補講を追加（指定日・時限に既存の授業を追加）

    Args:
        makeup_data: 補講情報

    Returns:
        成功メッセージ
    """
    if makeup_data.source_weekday not in WEEKDAY_MAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"無効な曜日です: {makeup_data.source_weekday}",
        )
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        try:
            record = add_makeup_class(
                user_data,
                makeup_data.date,
                makeup_data.period,
                WEEKDAY_MAP[makeup_data.source_weekday],
                makeup_data.source_period,
                room=makeup_data.room,
                term=makeup_data.term,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        save_user_data(user_id, user_data)

        # DM通知を送信
        msg = (
            f"補講を登録しました。\n\n"
            f"・日付: {record['date']}\n"
            f"・時限: {record['period']}限\n"
            f"・科目名: {record['subject']}\n"
            f"・教室: {record['room']}\n"
            f"・元の授業: {WEEKDAYS[record['day']]} {record['source_period']}限（{record['term']}）"
        )
        await notify_user_change(user_id, msg)

        return SuccessResponse(message="補講を追加しました", data={"makeup": record})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"補講追加エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="補講の追加に失敗しました",
        )


@router.delete("/api/makeup/{date}/{period}", response_model=SuccessResponse)
async def delete_makeup(
    date: str,
    period: str,
    source_weekday: str = None,
    source_period: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """
    補講を削除

    Args:
        date: 日付（YYYY-MM-DD）
        period: 時限
        source_weekday / source_period: 元の授業（同じ日・時限に補講が複数ある場合に指定）
    Returns:
        成功メッセージ
    """
    if source_weekday is not None and source_weekday not in WEEKDAY_MAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"無効な曜日です: {source_weekday}",
        )
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        try:
            removed = remove_makeup_class(
                user_data,
                date,
                period,
                WEEKDAY_MAP.get(source_weekday) if source_weekday else None,
                source_period,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        if removed is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="補講が見つかりません",
            )
        save_user_data(user_id, user_data)

        # DM通知を送信
        msg = (
            f"補講を削除しました。\n\n"
            f"・日付: {date}\n"
            f"・時限: {period}限\n"
            f"・科目名: {removed.get('subject', '不明')}"
        )
        await notify_user_change(user_id, msg)

        return SuccessResponse(message="補講を削除しました")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"補講削除エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="補講の削除に失敗しました",
        )


# ============ 休講（Cancel）エンドポイント ============


@router.get("/api/cancel", response_model=ListResponse)
async def get_cancel_classes(current_user: TokenData = Depends(get_current_user)):
    """
    休講一覧を取得

    Returns:
        休講リスト（日付・時限順）
    """
    try:
        user_data = load_user_data(current_user.user_id)
        cancel_classes = user_data.get("cancelled_classes", []) or []
        return ListResponse(count=len(cancel_classes), items=cancel_classes)
    except Exception as e:
        logger.exception(f"休講一覧取得エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="休講一覧の取得に失敗しました",
        )


@router.get("/api/cancel/candidates", response_model=ListResponse)
async def get_cancel_candidates(
    date: str,
    term: str = None,
    current_user: TokenData = Depends(get_current_user),
):
    """
    指定日に実施される通常授業（休講の候補）を取得

    Args:
        date: 日付（YYYY-MM-DD）
    """
    try:
        user_data = load_user_data(current_user.user_id)
        selected_term = (
            normalize_term_key(term) if term else get_term_for_date(user_data, date)
        )
        classes = get_regular_classes_on_date(user_data, selected_term, date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="日付形式が無効です。YYYY-MM-DD で指定してください",
        )
    items = sorted(
        (
            {
                "period": str(c.get("period")),
                "subject": c.get("subject"),
                "room": c.get("room"),
                "cancelled": is_class_cancelled(user_data, date, c.get("period")),
            }
            for c in classes
        ),
        key=lambda x: x["period"],
    )
    return ListResponse(count=len(items), items=items)


@router.post("/api/cancel", response_model=SuccessResponse)
async def add_cancel(
    cancel_data: CancelCreate,
    current_user: TokenData = Depends(get_current_user),
):
    """
    休講を追加（その日の授業を通知せず、授業回数にも数えない）

    Args:
        cancel_data: 休講情報

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        try:
            record = add_cancelled_class(
                user_data, cancel_data.date, cancel_data.period, term=cancel_data.term
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        save_user_data(user_id, user_data)

        # DM通知を送信
        msg = (
            f"休講を登録しました。\n\n"
            f"・日付: {record['date']}\n"
            f"・時限: {record['period']}限\n"
            f"・科目名: {record['subject']}\n"
            "※この授業は通知されず、授業回数にも数えません"
        )
        await notify_user_change(user_id, msg)

        return SuccessResponse(message="休講を追加しました", data={"cancel": record})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"休講追加エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="休講の追加に失敗しました",
        )


@router.delete("/api/cancel/{date}/{period}", response_model=SuccessResponse)
async def delete_cancel(
    date: str,
    period: str,
    current_user: TokenData = Depends(get_current_user),
):
    """
    休講を取り消し

    Args:
        date: 日付（YYYY-MM-DD）
        period: 時限

    Returns:
        成功メッセージ
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        removed = remove_cancelled_class(user_data, date, period)
        if removed is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="休講が見つかりません",
            )
        save_user_data(user_id, user_data)

        # DM通知を送信
        msg = (
            f"休講を取り消しました。\n\n"
            f"・日付: {date}\n"
            f"・時限: {period}限\n"
            f"・科目名: {removed.get('subject', '不明')}"
        )
        await notify_user_change(user_id, msg)

        return SuccessResponse(message="休講を取り消しました")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"休講削除エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="休講の取り消しに失敗しました",
        )


# ============ 曜日変更・教室変更（Overrides）エンドポイント ============


def _overrides_payload(user_data: dict) -> dict:
    return {
        "day_overrides": list_day_overrides(user_data),
        "room_overrides": list_room_overrides(user_data),
    }


@router.get("/api/overrides")
async def get_overrides(current_user: TokenData = Depends(get_current_user)):
    """曜日変更・教室変更の一覧を取得（Discord で登録したものを含む）"""
    return _overrides_payload(load_user_data(current_user.user_id))


@router.post("/api/overrides/day", response_model=SuccessResponse)
async def add_day_override(
    body: DayOverrideCreate, current_user: TokenData = Depends(get_current_user)
):
    """指定日を別の曜日の時間割にする（同じ日の設定は置き換える）"""
    user_id = current_user.user_id
    user_data = load_user_data(user_id)
    try:
        set_day_override(user_data, body.date, body.weekday)
    except ValueError as e:
        detail = str(e) if "曜日" in str(e) else "日付は「YYYY-MM-DD」の形式で入力してください"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    save_user_data(user_id, user_data)
    await notify_user_change(user_id, f"曜日変更を登録しました: {body.date} → {body.weekday} の時間割")
    return SuccessResponse(message="曜日変更を登録しました", data=_overrides_payload(user_data))


@router.delete("/api/overrides/day/{date}", response_model=SuccessResponse)
async def delete_day_override(
    date: str, current_user: TokenData = Depends(get_current_user)
):
    """曜日変更を取り消す"""
    user_id = current_user.user_id
    user_data = load_user_data(user_id)
    if not remove_day_override(user_data, date):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="該当する曜日変更が見つかりません"
        )
    save_user_data(user_id, user_data)
    await notify_user_change(user_id, f"曜日変更を取り消しました: {date}")
    return SuccessResponse(message="曜日変更を取り消しました", data=_overrides_payload(user_data))


@router.post("/api/overrides/room", response_model=SuccessResponse)
async def add_room_override(
    body: RoomOverrideCreate, current_user: TokenData = Depends(get_current_user)
):
    """指定日・時限の教室を変更する（同じ日・時限の設定は置き換える）"""
    user_id = current_user.user_id
    user_data = load_user_data(user_id)
    try:
        set_room_override(user_data, body.date, body.period, body.room)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    save_user_data(user_id, user_data)
    await notify_user_change(
        user_id, f"教室変更を登録しました: {body.date} {body.period}限 → {body.room.strip()}"
    )
    return SuccessResponse(message="教室変更を登録しました", data=_overrides_payload(user_data))


@router.delete("/api/overrides/room/{date}/{period}", response_model=SuccessResponse)
async def delete_room_override(
    date: str, period: str, current_user: TokenData = Depends(get_current_user)
):
    """教室変更を取り消す"""
    user_id = current_user.user_id
    user_data = load_user_data(user_id)
    if not remove_room_override(user_data, date, period):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="該当する教室変更が見つかりません"
        )
    save_user_data(user_id, user_data)
    await notify_user_change(user_id, f"教室変更を取り消しました: {date} {period}限")
    return SuccessResponse(message="教室変更を取り消しました", data=_overrides_payload(user_data))


@router.post("/api/overrides/bulk", response_model=SuccessResponse)
async def bulk_save_overrides(
    bulk_data: OverridesBulkSaveRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """
    曜日変更・教室変更を送られた内容で置き換える（送られなかった既存の設定は取り消す）
    """
    try:
        user_id = current_user.user_id
        user_data = load_user_data(user_id)
        replace_day_overrides(user_data, bulk_data.day_overrides)
        replace_room_overrides(user_data, bulk_data.room_overrides)
        save_user_data(user_id, user_data)

        result = _overrides_payload(user_data)
        await notify_user_change(
            user_id,
            "時間割の上書き設定を保存・更新しました。\n\n"
            f"・曜日上書き: {len(result['day_overrides'])}件\n"
            f"・教室上書き: {len(result['room_overrides'])}件",
        )
        return SuccessResponse(message="上書き設定を保存しました", data=result)
    except Exception as e:
        logger.exception(f"上書き設定一括保存エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="上書き設定の保存に失敗しました",
        )
