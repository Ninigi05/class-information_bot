"""
設定管理 API エンドポイント
"""

import os
import re
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from typing import Optional

import gmail_oauth
from account import delete_account
from mail_service import has_gmail_token, unlink_gmail
from utils import (
    load_user_data,
    save_user_data,
    PERIOD_TO_TIME,
    DEFAULT_NOTIFY,
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
    GmailSettingsSaveRequest,
    GmailAutoScanUpdate,
)
from web.api.notification import notify_user_change

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])
TIME_RE = re.compile(r"^(2[0-3]|[01]?\d):[0-5]\d$")


def _check_period_time(period: str, time: str) -> None:
    if period not in PERIOD_TO_TIME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"無効な時限です: {period}"
        )
    if not TIME_RE.match(time or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="時刻は「HH:MM」の形式で入力してください",
        )


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
        notify_settings = user_data.get("notify_settings", {}) or {}
        normal = notify_settings.get("normal") or DEFAULT_NOTIFY["normal"]
        exam = notify_settings.get("exam") or DEFAULT_NOTIFY["exam"]
        morning_time = user_data.get("morning_notice_time", "08:00")

        return SettingsShowResponse(
            period_times=period_times,
            exam_period_times=user_data.get("exam_period_overrides", {}) or {},
            term_settings=term_settings,
            notify={
                "normal_first": normal.get("first", 15),
                "normal_second": normal.get("second", 10),
                "exam_first": exam.get("first", 30),
                "exam_second": exam.get("second", 25),
                "morning_time": morning_time,
            },
            gmail={
                "linked": has_gmail_token(user_id),
                "auto_scan": user_data.get("mail_auto_scan", True) is not False,
            },
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
        _check_period_time(period_data.period, period_data.time)
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


@router.delete("/period-time/{period}", response_model=SuccessResponse)
async def reset_period_time(
    period: str, current_user: TokenData = Depends(get_current_user)
):
    """時限の開始時刻を初期値に戻す"""
    user_id = current_user.user_id
    data = load_user_data(user_id)
    overrides = data.get("period_overrides", {}) or {}
    if overrides.pop(period, None) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{period}限には設定がありません"
        )
    data["period_overrides"] = overrides
    save_user_data(user_id, data)
    default = PERIOD_TO_TIME.get(period, "未設定")
    await notify_user_change(user_id, f"{period}限の開始時刻を初期値（{default}）に戻しました。")
    return SuccessResponse(message=f"{period}限の開始時刻を初期値に戻しました")


@router.post("/exam-period-time", response_model=SuccessResponse)
async def update_exam_period_time(
    period_data: PeriodTimeUpdate,
    current_user: TokenData = Depends(get_current_user),
):
    """試験期間中の時限の開始時刻を設定"""
    _check_period_time(period_data.period, period_data.time)
    user_id = current_user.user_id
    data = load_user_data(user_id)
    data.setdefault("exam_period_overrides", {})[period_data.period] = period_data.time
    save_user_data(user_id, data)
    await notify_user_change(
        user_id,
        f"試験期間中の{period_data.period}限の開始時刻を {period_data.time} に設定しました。",
    )
    return SuccessResponse(message="試験期間中の開始時刻を設定しました")


@router.delete("/exam-period-time/{period}", response_model=SuccessResponse)
async def reset_exam_period_time(
    period: str, current_user: TokenData = Depends(get_current_user)
):
    """試験期間中の時限の開始時刻の設定を取り消す"""
    user_id = current_user.user_id
    data = load_user_data(user_id)
    overrides = data.get("exam_period_overrides", {}) or {}
    if overrides.pop(period, None) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"試験期間中の{period}限には設定がありません",
        )
    data["exam_period_overrides"] = overrides
    save_user_data(user_id, data)
    await notify_user_change(user_id, f"試験期間中の{period}限の開始時刻の設定を取り消しました。")
    return SuccessResponse(message="試験期間中の開始時刻の設定を取り消しました")


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
            if n.morning_time and not TIME_RE.match(n.morning_time):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="朝の通知時刻は「HH:MM」の形式で入力してください",
                )
            user_notify = data.setdefault("notify_settings", {})

            # 通常通知
            normal = user_notify.setdefault("normal", dict(DEFAULT_NOTIFY["normal"]))
            if n.normal_first is not None:
                normal["first"] = n.normal_first
            if n.normal_second is not None:
                normal["second"] = n.normal_second

            # 試験期間の通知
            exam = user_notify.setdefault("exam", dict(DEFAULT_NOTIFY["exam"]))
            if n.exam_first is not None:
                exam["first"] = n.exam_first
            if n.exam_second is not None:
                exam["second"] = n.exam_second

            # 朝の通知時刻
            if n.morning_time:
                data["morning_notice_time"] = n.morning_time

        save_user_data(user_id, data)
        logger.info(f"通知設定更新: user_id={user_id}")

        # DM通知を送信
        new_notify = data.get("notify_settings", {}).get("normal", {})
        new_exam = data.get("notify_settings", {}).get("exam", {})
        new_morning = data.get("morning_notice_time", "08:00")
        msg = (
            f"通知タイミング設定を更新しました。\n\n"
            f"■ 変更後の設定:\n"
            f"・1回目の通常通知: {new_notify.get('first')}分前 (変更前: {old_notify.get('first')}分前)\n"
            f"・2回目の通常通知: {new_notify.get('second')}分前 (変更前: {old_notify.get('second')}分前)\n"
            f"・試験期間の通知: {new_exam.get('first')}分前 / {new_exam.get('second')}分前\n"
            f"・朝の通知時刻: {new_morning} (変更前: {old_morning})"
        )
        await notify_user_change(user_id, msg)

        return SuccessResponse(message="通知設定を更新しました", data=None)
    except HTTPException:
        raise
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



@router.get("/gmail/auth-url")
async def get_gmail_auth_url(current_user: TokenData = Depends(get_current_user)):
    """
    ログイン中の利用者に紐付けた Gmail 認証URLを発行する（本人以外はコードを交換できない）
    """
    credentials_file = gmail_oauth.resolve_credentials_path()
    if not credentials_file or not os.path.exists(credentials_file):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GMAIL_CREDENTIALS が見つかりません。サーバー設定を確認してください。",
        )
    try:
        auth_url, state = gmail_oauth.create_auth_url(
            credentials_file, current_user.user_id
        )
        return {"auth_url": auth_url, "state": state}
    except Exception as e:
        logger.exception(f"Gmail auth URL 発行失敗: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gmail 認証URLの生成に失敗しました",
        )


@router.post("/gmail", response_model=SuccessResponse)
async def update_gmail_auth(
    gmail_data: GmailSettingsSaveRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """
    Gmail 認証コードをトークンに交換して連携を完了する

    認証URL発行時の code_verifier と照合するため、本人が発行した認証のコードしか受け付けない。
    """
    user_id = current_user.user_id
    try:
        await run_in_threadpool(
            gmail_oauth.exchange_code,
            gmail_oauth.resolve_credentials_path(),
            user_id,
            gmail_data.gmail_auth_code,
            gmail_data.gmail_auth_state,
        )
    except gmail_oauth.GmailAuthError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    try:
        # 以前の実装で保存していた認証コードが残っていれば削除する
        data = load_user_data(user_id)
        if data.pop("gmail_auth_code", None) is not None:
            save_user_data(user_id, data)

        logger.info(f"Gmail連携完了（Web）: user_id={user_id}")
        await notify_user_change(
            user_id,
            "【Gmail連携完了通知】\n\nWebダッシュボードから Gmail 認証が正常に完了しました。",
        )
        return SuccessResponse(message="Gmail連携が完了しました", data=None)
    except Exception as e:
        logger.exception(f"Gmail認証コード保存エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gmail認証コードの保存に失敗しました",
        )


@router.delete("/gmail", response_model=SuccessResponse)
async def unlink_gmail_account(current_user: TokenData = Depends(get_current_user)):
    """Gmail 連携を解除し、保存しているトークンを削除する"""
    user_id = current_user.user_id
    removed = await run_in_threadpool(unlink_gmail, user_id)
    data = load_user_data(user_id)
    for key in ("mail_seen_ids", "mail_auth_error_notified"):
        data.pop(key, None)
    save_user_data(user_id, data)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Gmail は連携されていません"
        )
    await notify_user_change(user_id, "Gmail 連携を解除し、保存していた認証情報を削除しました。")
    return SuccessResponse(message="Gmail 連携を解除しました")


@router.post("/gmail/auto-scan", response_model=SuccessResponse)
async def update_gmail_auto_scan(
    body: GmailAutoScanUpdate, current_user: TokenData = Depends(get_current_user)
):
    """Gmail の自動確認（新着の休講・授業変更を DM で通知）を切り替える"""
    user_id = current_user.user_id
    data = load_user_data(user_id)
    data["mail_auto_scan"] = body.enabled
    save_user_data(user_id, data)
    return SuccessResponse(
        message="Gmail の自動確認を有効にしました" if body.enabled else "Gmail の自動確認を停止しました"
    )


@router.delete("/account", response_model=SuccessResponse)
async def delete_account_data(current_user: TokenData = Depends(get_current_user)):
    """保存している利用者のデータ（時間割・設定・Gmail 連携など）をすべて削除する"""
    user_id = current_user.user_id
    try:
        await run_in_threadpool(delete_account, user_id)
    except Exception as e:
        logger.exception(f"データ削除エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="データの削除に失敗しました",
        )
    await notify_user_change(
        user_id, "Webダッシュボードから、保存していたデータをすべて削除しました。"
    )
    return SuccessResponse(message="データをすべて削除しました")
