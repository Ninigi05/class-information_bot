"""
FastAPI Web統合モジュール
Discord Bot と並行して動作する Web API サーバー
"""

import logging
import os
from threading import Thread
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field
import gmail_oauth

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder

from utils import TERM_FIRST, normalize_term_key
from web_link_service import create_link_key

logger = logging.getLogger(__name__)

# Discord OAuth2 関連のインポート
try:
    from web.auth import authenticate_user, get_current_user, TokenData, get_login_url
    from web.schemas import AuthResponse, UserInfo, SuccessResponse, LoginUrlResponse
    HAS_AUTH = True
    logger.info("[Web Init] OAuth機能のインポートに成功しました (HAS_AUTH=True)")
except Exception as e:
    import traceback
    print(f"[Web Init CRITICAL ERROR] OAuth機能インポート失敗。理由: {e}", flush=True)
    logger.error(f"[Web Init] OAuth機能インポート失敗。理由: {e}")
    logger.error(traceback.format_exc())
    HAS_AUTH = False

# WebダッシュボードのAPIルーターのインポート
try:
    from web.api import classes, exams, makeup, settings
    HAS_API_ROUTERS = True
    logger.info("[Web Init] APIルーターのインポートに成功しました (HAS_API_ROUTERS=True)")
except Exception as e:
    import traceback
    logger.error(f"[Web Init] APIルーターのインポートに失敗しました。理由: {e}")
    logger.error(traceback.format_exc())
    HAS_API_ROUTERS = False

# FastAPI アプリケーションの初期化
# API ドキュメント（/docs, /redoc, /openapi.json）は DEBUG=true のときだけ公開する
_EXPOSE_API_DOCS = (os.getenv("DEBUG") or "").strip().lower() == "true"

web_app = FastAPI(
    title="Discord授業情報Bot - Web API",
    description="Discord Bot のデータベースをWeb経由で操作",
    version="1.0.0",
    docs_url="/docs" if _EXPOSE_API_DOCS else None,
    redoc_url="/redoc" if _EXPOSE_API_DOCS else None,
    openapi_url="/openapi.json" if _EXPOSE_API_DOCS else None,
)

# ============ セキュリティヘッダー ============
# ページのスクリプトは自サーバーから配信する（web/vendor/）。インラインスクリプトがあるため
# script-src に 'unsafe-inline' を含むが、外部オリジンからの読み込み・通信は禁止する
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "media-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
HSTS_HEADER = "max-age=31536000"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@web_app.middleware("http")
async def security_headers_middleware(request, call_next):
    # Cloudflare Tunnel などのプロキシ越しに HTTP で来たアクセスは HTTPS へリダイレクトする
    # （ローカルからの直接アクセスにはヘッダーが付かないため、従来どおり HTTP で使える）
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    is_https = request.url.scheme == "https" or forwarded_proto == "https"
    if forwarded_proto == "http" and request.url.hostname not in _LOCAL_HOSTS:
        return RedirectResponse(
            str(request.url.replace(scheme="https", port=None)), status_code=308
        )

    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if is_https:
        response.headers.setdefault("Strict-Transport-Security", HSTS_HEADER)
    return response

if HAS_API_ROUTERS:
    web_app.include_router(classes.router)
    web_app.include_router(exams.router)
    web_app.include_router(makeup.router)
    web_app.include_router(settings.router)

# 静的ファイル配信（catch_all から web/ 配下のファイルを返す）
WEB_ROOT = os.path.realpath("web")
SERVABLE_EXTENSIONS = {
    ".html", ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".mp4",
}

# ============ 認証エンドポイント ============

if HAS_AUTH:
    @web_app.get("/api/auth/url", response_model=LoginUrlResponse)
    async def get_auth_url_endpoint():
        """
        Discord OAuth2 認証 URL と state を取得 (手順1)
        """
        logger.info("[Web Auth] 認証URLのリクエストを受信しました")
        try:
            url, state = get_login_url()
            logger.info(f"[Web Auth] 認証URLを生成しました: {url} (state: {state})")
            return LoginUrlResponse(url=url, state=state)
        except Exception as e:
            logger.exception(f"[Web Auth] 認証URL生成エラー: {e}")
            raise HTTPException(status_code=500, detail=str(e))


    @web_app.post("/api/auth/login", response_model=AuthResponse)
    async def login(code: str):
        """
        Discord OAuth2 コードを使用してログイン (手順3, 4, 5)
        """
        logger.info(f"[Web Auth] ログインリクエストを受信しました: code={code[:5]}***")
        try:
            auth_response = await authenticate_user(code)
            logger.info(
                f"[Web Auth] ユーザーがログインしました: {auth_response.user.user_id} ({auth_response.user.username})"
            )
            return auth_response
        except HTTPException as he:
            logger.warning(f"[Web Auth] ログイン失敗（HTTPException）: {he.status_code} - {he.detail}")
            raise
        except Exception as e:
            logger.exception(f"[Web Auth] ログインエラー: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="ログインに失敗しました",
            )


    @web_app.get("/api/auth/me", response_model=UserInfo)
    async def get_current_user_info(current_user: TokenData = Depends(get_current_user)):
        """
        現在ログインしているユーザーの情報を取得
        """
        logger.info(f"ユーザー情報リクエスト: user_id={current_user.user_id}")
        return UserInfo(
            user_id=current_user.user_id,
            username=current_user.username,
            avatar=None,
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
    term_ranges: dict[str, dict[str, str]] = Field(default_factory=dict)
    class_count_targets: dict[str, int] = Field(default_factory=dict)
    gmail_auth_code: Optional[str] = None
    gmail_auth_state: Optional[str] = None


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
            "term_ranges": draft.term_ranges,
            "class_count_targets": draft.class_count_targets,
        },
        "overrides": {
            "day_overrides": draft.day_overrides,
            "room_overrides": draft.room_overrides,
        },
        "gmail": {
            "gmail_auth_code": draft.gmail_auth_code,
            "gmail_auth_state": draft.gmail_auth_state,
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


# ============ ヘルスチェック ============


@web_app.get("/api/health")
async def health_check():
    """ヘルスチェック"""
    return {"status": "ok", "service": "Discord授業情報Bot API"}


@web_app.get("/api/public/gmail-auth-url")
async def get_public_gmail_auth_url():
    """Web 画面向け Gmail 認証URLを発行する（ログイン不要ページ用）。

    返す state は、連携キー発行時に code と一緒に送ってもらい、
    /web applykey でのコード交換時に発行時の code_verifier と照合する（PKCE）。
    """
    credentials_file = gmail_oauth.resolve_credentials_path()
    if not credentials_file or not os.path.exists(credentials_file):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GMAIL_CREDENTIALS が見つかりません。サーバー設定を確認してください。",
        )
    try:
        auth_url, state = gmail_oauth.create_auth_url(credentials_file)
        return {
            "auth_url": auth_url,
            "state": state,
            "redirect_uri": gmail_oauth.get_redirect_uri(),
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


@web_app.get("/{rest_of_path:path}")
async def catch_all(rest_of_path: str):
    logger.info(f"[Web Request] catch_all受信: {rest_of_path}")
    if rest_of_path.startswith("api/"):
        logger.warning(f"[Web Request] APIエンドポイントが見つかりません: {rest_of_path}")
        raise HTTPException(status_code=404, detail="Not Found")

    # ルートアクセスを正しく処理
    if not rest_of_path or rest_of_path == ".":
        logger.info("[Web Request] トップページ（index.html）を配信します")
        return FileResponse("web/index.html")

    # 特定の画面遷移ルートの処理
    clean_path = rest_of_path.strip("/")
    if clean_path in ["login", "login/"]:
        template_path = "web/templates/login.html"
        if os.path.exists(template_path):
            logger.info(f"[Web Request] ログインページを配信します: {template_path}")
            return FileResponse(template_path)
    elif clean_path == "dashboard":
        template_path = "web/templates/dashboard.html"
        if os.path.exists(template_path):
            logger.info(f"[Web Request] ダッシュボードページを配信します: {template_path}")
            return FileResponse(template_path)
    elif clean_path == "auth/callback":
        template_path = "web/templates/callback.html"
        if os.path.exists(template_path):
            logger.info(f"[Web Request] コールバックページを配信します: {template_path}")
            return FileResponse(template_path)

    # 実パスに解決し、web/ の外（../ によるパストラバーサル）や配信対象外の拡張子は返さない
    file_path = os.path.realpath(os.path.join(WEB_ROOT, rest_of_path))
    if (
        os.path.commonpath([WEB_ROOT, file_path]) == WEB_ROOT
        and os.path.splitext(file_path)[1].lower() in SERVABLE_EXTENSIONS
        and os.path.isfile(file_path)
    ):
        logger.info(f"[Web Request] 物理ファイルを配信します: {file_path}")
        return FileResponse(file_path)

    logger.info(f"[Web Request] マッチするファイルがないため、index.html にフォールバックします (path={rest_of_path})")
    return FileResponse("web/index.html")


# ============ Web サーバーの起動・管理 ============


def start_web_server(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn

    config = uvicorn.Config(app=web_app, host=host, port=port, log_level="info")
    thread = Thread(target=uvicorn.Server(config).run, daemon=True)
    thread.start()
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
