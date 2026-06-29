"""
FastAPI Web統合モジュール
Discord Bot と並行して動作する Web API サーバー
"""

import logging
from threading import Thread
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
import os
import glob
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field
from google_auth_oauthlib.flow import Flow
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from utils import (
    load_user_data,
    save_user_data,
    WEEKDAYS,
    PERIOD_TO_TIME,
    WEEKDAY_MAP,
    TERM_FIRST,
    TERM_SECOND,
    normalize_term_key,
)
from api_security import verify_api_key
from web_link_service import create_link_key

logger = logging.getLogger(__name__)

# FastAPI アプリケーションの初期化
web_app = FastAPI(
    title="Discord授業情報Bot - Web API",
    description="Discord Bot のデータベースをWeb経由で操作",
    version="1.0.0",
)

# webフォルダにある静的ファイル(css/js/画像等)を /static で配信
web_app.mount("/static", StaticFiles(directory="web"), name="static")

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

DEFAULT_CORS_REGEX = r"^https://([a-z0-9-]+\.)*github\.io$"


def _normalize_origin(value: str) -> str:
    """末尾スラッシュを除去して CORS 比較しやすい形に揃える。"""
    return value.strip().rstrip("/")


def _parse_extra_cors_origins() -> list[str]:
    """環境変数のカンマ区切り CORS 許可リストを展開する。"""
    raw = os.getenv("CORS_ORIGIN_GITHUB_PAGES", "")
    if not raw:
        return []
    return [_normalize_origin(item) for item in raw.split(",") if item.strip()]


def _get_tunnel_origin() -> str:
    """Cloudflare Quick Tunnel の公開URLを取得する。"""
    value = os.getenv("TUNNEL_PUBLIC_BASE_URL", "").strip()
    if not value:
        return ""
    return _normalize_origin(value)


def _build_cors_origin_regex() -> str:
    """必要に応じて CORS の正規表現を組み立てる。"""
    user_regex = os.getenv("CORS_ORIGIN_REGEX", "").strip()
    if user_regex:
        return user_regex
    return DEFAULT_CORS_REGEX


# 空の文字列を除外し重複を除去
CORS_ORIGINS = list(
    dict.fromkeys(
        [
            *[_normalize_origin(x) for x in DEFAULT_CORS_ORIGINS],
            *_parse_extra_cors_origins(),
            _get_tunnel_origin(),
        ]
    )
)
CORS_ORIGINS = [x for x in CORS_ORIGINS if x]

web_app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=_build_cors_origin_regex(),
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


class WebClassDraft(BaseModel):
    weekday: str
    period: str
    subject: str
    room: str


class NotifyDraft(BaseModel):
    normal_first: int = 15
    normal_second: int = 10
    exam_first: int = 30
    exam_second: int = 25
    morning_time: str = "08:00"


class DayOverrideDraft(BaseModel):
    date: str
    weekday: str


class RoomOverrideDraft(BaseModel):
    date: str
    period: str
    room: str


class WebExamClassDraft(BaseModel):
    weekday: str
    period: str
    subject: str
    room: str
    time: Optional[str] = None


class WebExamScheduleDraft(BaseModel):
    name: str
    start: str
    end: str
    classes: list[WebExamClassDraft] = Field(default_factory=list)


class WebRegistrationDraft(BaseModel):
    classes: list[WebClassDraft] = Field(default_factory=list)
    classes_by_term: dict[str, list[WebClassDraft]] = Field(default_factory=dict)
    period_overrides: dict[str, str] = Field(default_factory=dict)
    notify: NotifyDraft = Field(default_factory=NotifyDraft)
    day_overrides: list[DayOverrideDraft] = Field(default_factory=list)
    room_overrides: list[RoomOverrideDraft] = Field(default_factory=list)
    exam_schedules: list[WebExamScheduleDraft] = Field(default_factory=list)
    exam_period_overrides: dict[str, str] = Field(default_factory=dict)
    term_start_dates: dict[str, str] = Field(default_factory=dict)
    class_count_targets: dict[str, int] = Field(default_factory=dict)
    gmail_auth_code: Optional[str] = None


class LinkKeyIssueRequest(BaseModel):
    feature: Literal[
        "all",
        "classes",
        "notify",
        "overrides",
        "gmail",
        "exam",
    ] = "all"
    data: WebRegistrationDraft


def _build_feature_payload(
    draft: WebRegistrationDraft,
    feature: Literal["all", "classes", "notify", "overrides", "gmail", "exam"],
    term: str = TERM_FIRST,
) -> dict[str, Any]:
    normalized_term = normalize_term_key(term)
    classes_for_term = list(draft.classes)
    if not classes_for_term and isinstance(draft.classes_by_term, dict):
        classes_for_term = (
            draft.classes_by_term.get(normalized_term)
            or draft.classes_by_term.get(
                "1st" if normalized_term == TERM_FIRST else "2nd"
            )
            or []
        )

    notify = draft.notify.model_dump()
    draft_all = draft.model_dump()
    draft_all["classes"] = classes_for_term

    payload_by_feature: dict[str, dict[str, Any]] = {
        "all": draft_all,
        "classes": {
            "classes": classes_for_term,
        },
        "notify": {
            "notify": {
                "normal_first": notify.get("normal_first"),
                "normal_second": notify.get("normal_second"),
                "morning_time": notify.get("morning_time"),
            },
            "period_overrides": draft.period_overrides,
            "term_start_dates": draft.term_start_dates,
            "class_count_targets": draft.class_count_targets,
        },
        "overrides": {
            "day_overrides": draft.day_overrides,
            "room_overrides": draft.room_overrides,
        },
        "gmail": {
            "gmail_auth_code": draft.gmail_auth_code,
        },
        "exam": {
            "exam_schedules": draft.exam_schedules,
            "exam_period_overrides": draft.exam_period_overrides,
            "notify": {
                "exam_first": notify.get("exam_first"),
                "exam_second": notify.get("exam_second"),
            },
        },
    }
    out = payload_by_feature.get(feature, draft_all)
    out["_meta"] = {"feature": feature, "term": normalized_term}
    # Persisted link payload must be JSON-serializable.
    return jsonable_encoder(out)


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


def _resolve_gmail_credentials_path() -> str:
    """環境差分（ローカル/コンテナ）を吸収して credentials パスを解決。"""
    env_val = (os.getenv("GMAIL_CREDENTIALS") or "").strip()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search_roots = [os.getcwd(), script_dir, "/app"]

    if env_val:
        if os.path.isabs(env_val):
            return env_val
        candidates = [os.path.join(root, env_val) for root in search_roots]
        for path in candidates:
            if os.path.exists(path):
                return path

    # Fallback autodiscovery for common Google OAuth client secret filenames.
    patterns = [
        "client_secret*.json",
        "credentials.json",
        "gmail_credentials.json",
    ]
    for root in search_roots:
        for pattern in patterns:
            matched = sorted(glob.glob(os.path.join(root, pattern)))
            if matched:
                logger.info("GMAIL_CREDENTIALS を自動検出: %s", matched[0])
                return matched[0]

    if env_val:
        return os.path.join(script_dir, env_val)
    return ""


# ============ ヘルスチェック ============


@web_app.get("/")
async def read_index():
    return FileResponse("web/index.html")


@web_app.get("/api/health")
async def health_check():
    """ヘルスチェック"""
    return {"status": "ok", "service": "Discord授業情報Bot API"}


@web_app.get("/api/public/gmail-auth-url")
async def get_public_gmail_auth_url():
    """Web 画面向け Gmail 認証URLを発行する。"""
    credentials_file = _resolve_gmail_credentials_path()
    if not credentials_file or not os.path.exists(credentials_file):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GMAIL_CREDENTIALS が見つかりません。サーバー設定を確認してください。",
        )
    try:
        redirect_uri = os.getenv(
            "GMAIL_REDIRECT_URI", "https://ninigi05.github.io/oauth-redirect/"
        )
        flow = Flow.from_client_secrets_file(
            credentials_file,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            redirect_uri=redirect_uri,
        )
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        return {
            "auth_url": auth_url,
            "redirect_uri": redirect_uri,
            "message": "認証後に表示された code を Web画面に貼り付けてください。",
        }
    except Exception as e:
        logger.exception(f"Gmail auth URL 発行失敗: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gmail 認証URLの生成に失敗しました",
        )


@web_app.post("/api/public/issue-link-key")
async def issue_link_key(payload: dict[str, Any]):
    """Webで登録した内容を機能単位キー化し、Discord 側取り込み用キーを返す。"""
    try:
        # 互換対応: 旧形式（WebRegistrationDraft 直送）も受け付ける
        if "data" in payload:
            req = LinkKeyIssueRequest(**payload)
            feature = req.feature
            draft = req.data
        else:
            draft = WebRegistrationDraft(**payload)
            feature = "all"

        # Extract term from _meta if present
        term = normalize_term_key(
            (payload.get("_meta") or {}).get("term") or TERM_FIRST
        )

        link_payload = _build_feature_payload(draft, feature, term)
        key, expires_at = create_link_key(link_payload)
        return {
            "ok": True,
            "link_key": key,
            "expires_at": expires_at,
            "feature": feature,
            "discord_command": f"/web applykey key:{key}",
        }
    except Exception as e:
        logger.exception(f"リンクキー生成失敗: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="リンクキーの発行に失敗しました",
        )


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
        logger.info(
            "授業 upsert: user_id=%s weekday=%s period=%s action=%s",
            user_id,
            payload.weekday,
            payload.period,
            action,
        )

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
        logger.info(
            "授業 delete: user_id=%s weekday=%s period=%s", user_id, weekday, period
        )

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
        table: dict[str, dict[str, Any | None]] = {}
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
