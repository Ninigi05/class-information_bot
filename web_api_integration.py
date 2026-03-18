"""
FastAPI Web統合モジュール
Discord Bot と並行して動作する Web API サーバー
"""

import logging
from threading import Thread
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from typing import Optional
from pydantic import BaseModel, Field

from utils import load_user_data, save_user_data, WEEKDAYS, PERIOD_TO_TIME, WEEKDAY_MAP
from api_security import verify_api_key

logger = logging.getLogger(__name__)

# FastAPI アプリケーションの初期化
web_app = FastAPI(
    title="Discord授業情報Bot - Web API",
    description="Discord Bot のデータベースをWeb経由で操作",
    version="1.0.0",
)

# ============ CORS 設定 ============
# GitHub Pages など複数のドメインから利用可能に
DEFAULT_CORS_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    # GitHub Pages 用（自分のリポジトリに合わせて実際のURLに変更）
    "https://username.github.io",
]


def _parse_extra_cors_origins() -> list[str]:
    """環境変数のカンマ区切り CORS 許可リストを展開する。"""
    raw = os.getenv("CORS_ORIGIN_GITHUB_PAGES", "")
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]

# 空の文字列を除外し重複を除去
CORS_ORIGINS = list(dict.fromkeys([*DEFAULT_CORS_ORIGINS, *_parse_extra_cors_origins()]))

web_app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-API-Version"],
)


class ClassUpsertRequest(BaseModel):
    """授業の追加/更新リクエスト。"""

    weekday: str = Field(..., description="曜日（例: 月曜日）")
    period: str = Field(..., description="時限（例: 1）")
    subject: str = Field(..., min_length=1, description="授業名")
    room: str = Field(..., min_length=1, description="教室")


def _validate_weekday_and_period(weekday: str, period: str) -> None:
    if weekday not in WEEKDAY_MAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"無効な曜日です: {weekday}",
        )
    if str(period) not in PERIOD_TO_TIME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"無効な時限です: {period}",
        )


def _find_class_index(classes: list[dict], day: int, period: str) -> int:
    for i, c in enumerate(classes):
        if int(c.get("day", -1)) == day and str(c.get("period")) == str(period):
            return i
    return -1


# ============ ヘルスチェック ============


@web_app.get("/api/health")
async def health_check():
    """ヘルスチェック"""
    return {"status": "ok", "service": "Discord授業情報Bot API"}


# ============ ユーザーデータエンドポイント ============


@web_app.get("/api/user/{user_id}/classes")
async def get_user_classes(
    user_id: int,
    _: bool = Depends(verify_api_key),
):
    """
    特定ユーザーの授業一覧を取得

    Args:
        user_id: Discord ユーザーID
        _: API_KEY 検証（依存関数）

    Returns:
        授業データ
    """
    try:
        user_data = load_user_data(user_id)
        classes = user_data.get("classes", [])

        # weekday 番号を日本語に変換（表示用）
        for cls in classes:
            if "day" in cls and isinstance(cls["day"], int):
                day_num = cls["day"]
                if 0 <= day_num < len(WEEKDAYS):
                    cls["weekday_name"] = WEEKDAYS[day_num]

        logger.info(f"授業一覧取得: user_id={user_id}, count={len(classes)}")

        return {
            "user_id": user_id,
            "count": len(classes),
            "classes": classes,
            "weekdays": WEEKDAYS,
            "period_to_time": PERIOD_TO_TIME,
        }
    except Exception as e:
        logger.exception(f"授業一覧取得エラー (user_id={user_id}): {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="授業データの取得に失敗しました",
        )


@web_app.post("/api/user/{user_id}/classes/upsert")
async def upsert_user_class(
    user_id: int,
    payload: ClassUpsertRequest,
    _: bool = Depends(verify_api_key),
):
    """授業を追加、存在する場合は上書きする。"""
    try:
        _validate_weekday_and_period(payload.weekday, payload.period)
        user_data = load_user_data(user_id)
        classes = user_data.get("classes", [])
        day = WEEKDAY_MAP[payload.weekday]
        idx = _find_class_index(classes, day, payload.period)

        item = {
            "day": day,
            "period": str(payload.period),
            "subject": payload.subject.strip(),
            "room": payload.room.strip(),
        }

        if idx >= 0:
            classes[idx] = item
            action = "updated"
        else:
            classes.append(item)
            action = "created"

        user_data["classes"] = classes
        save_user_data(user_id, user_data)
        logger.info("授業 upsert: user_id=%s weekday=%s period=%s action=%s", user_id, payload.weekday, payload.period, action)

        return {
            "ok": True,
            "action": action,
            "class": {**item, "weekday_name": payload.weekday},
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"授業 upsert エラー (user_id={user_id}): {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="授業データの保存に失敗しました",
        )


@web_app.delete("/api/user/{user_id}/classes/{weekday}/{period}")
async def delete_user_class(
    user_id: int,
    weekday: str,
    period: str,
    _: bool = Depends(verify_api_key),
):
    """指定曜日・時限の授業を削除する。"""
    try:
        _validate_weekday_and_period(weekday, period)
        user_data = load_user_data(user_id)
        classes = user_data.get("classes", [])
        day = WEEKDAY_MAP[weekday]
        idx = _find_class_index(classes, day, period)
        if idx < 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="削除対象の授業が見つかりません",
            )

        removed = classes.pop(idx)
        user_data["classes"] = classes
        save_user_data(user_id, user_data)
        logger.info("授業 delete: user_id=%s weekday=%s period=%s", user_id, weekday, period)

        return {
            "ok": True,
            "deleted": {**removed, "weekday_name": weekday},
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"授業 delete エラー (user_id={user_id}): {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="授業データの削除に失敗しました",
        )


@web_app.get("/api/user/{user_id}/schedule-table")
async def get_schedule_table(
    user_id: int,
    _: bool = Depends(verify_api_key),
):
    """
    時間割テーブル形式でデータを取得

    Args:
        user_id: Discord ユーザーID
        _: API_KEY 検証（依存関数）

    Returns:
        {weekday: {period: class_info}} 形式
    """
    try:
        user_data = load_user_data(user_id)
        classes = user_data.get("classes", [])

        # テーブル形式に変換
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

        logger.info(f"時間割テーブル取得: user_id={user_id}")

        return {
            "user_id": user_id,
            "table": table,
            "weekdays": WEEKDAYS,
            "periods": list(PERIOD_TO_TIME.keys()),
            "period_to_time": PERIOD_TO_TIME,
        }
    except Exception as e:
        logger.exception(f"時間割テーブル取得エラー (user_id={user_id}): {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="時間割データの取得に失敗しました",
        )


@web_app.get("/api/user/{user_id}/all-data")
async def get_user_all_data(
    user_id: int,
    _: bool = Depends(verify_api_key),
):
    """
    ユーザーのすべてのデータを取得

    Args:
        user_id: Discord ユーザーID
        _: API_KEY 検証（依存関数）

    Returns:
        ユーザーの全データ
    """
    try:
        user_data = load_user_data(user_id)

        logger.info(f"全データ取得: user_id={user_id}")

        return {
            "user_id": user_id,
            "data": user_data,
            "constants": {
                "weekdays": WEEKDAYS,
                "period_to_time": PERIOD_TO_TIME,
            },
        }
    except Exception as e:
        logger.exception(f"全データ取得エラー (user_id={user_id}): {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ユーザーデータの取得に失敗しました",
        )


@web_app.get("/api/constants")
async def get_constants(
    _: bool = Depends(verify_api_key),
):
    """
    アプリケーション定数を取得
    （曜日、時限情報等）

    Returns:
        定数データ
    """
    return {
        "weekdays": WEEKDAYS,
        "period_to_time": PERIOD_TO_TIME,
        "periods": list(PERIOD_TO_TIME.keys()),
    }


# ============ エラーハンドリング ============


@web_app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """HTTP例外のハンドリング"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "status_code": exc.status_code,
        },
    )


@web_app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """一般例外のハンドリング"""
    logger.exception(f"予期しないエラー: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "detail": "内部サーバーエラーが発生しました",
            "status_code": 500,
        },
    )


# ============ Web サーバーの起動・管理 ============


def start_web_server(host: str = "0.0.0.0", port: int = 8000):
    """
    FastAPI Web サーバーをスレッドで起動
    （Discord Bot と並行実行するため）

    Args:
        host: バインドするホストアドレス
        port: バインドするポート番号
    """
    import uvicorn

    config = uvicorn.Config(
        app=web_app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    # スレッドで実行
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    logger.info(f"Web API サーバーを起動しました: {host}:{port}")
    return thread


async def start_web_server_async(host: str = "0.0.0.0", port: int = 8000):
    """
    FastAPI Web サーバーをasyncで起動
    （既存の async コンテキストで使用）

    Args:
        host: バインドするホストアドレス
        port: バインドするポート番号
    """
    import uvicorn

    config = uvicorn.Config(
        app=web_app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()
