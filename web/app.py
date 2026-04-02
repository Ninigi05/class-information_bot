"""
FastAPI Webダッシュボード メインアプリケーション
"""

import logging
from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import os

from web.config import ALLOWED_ORIGINS, LOG_LEVEL, BASE_DIR
from web.auth import authenticate_user, get_current_user, TokenData
from web.schemas import AuthResponse, UserInfo, SuccessResponse
from web.api import classes, exams, makeup, settings

# ロギング設定
logging.basicConfig(level=getattr(logging, LOG_LEVEL))
logger = logging.getLogger(__name__)

# FastAPI アプリケーションの初期化
app = FastAPI(
    title="Discord授業情報Bot - Webダッシュボード",
    description="Webブラウザから時間割を編集できるダッシュボード",
    version="1.0.0",
)

# CORS ミドルウェアの設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静的ファイルの設定
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ============ 認証エンドポイント ============


@app.post("/auth/login", response_model=AuthResponse)
async def login(code: str):
    """
    Discord OAuth2 コードを使用してログイン

    Args:
        code: Discord から返された認可コード

    Returns:
        認証情報（トークン、ユーザー情報）
    """
    try:
        auth_response = await authenticate_user(code)
        logger.info(
            f"ユーザーがログインしました: {auth_response.user.user_id} ({auth_response.user.username})"
        )
        return auth_response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"ログインエラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ログインに失敗しました",
        )


@app.get("/auth/me", response_model=UserInfo)
async def get_current_user_info(current_user: TokenData = Depends(get_current_user)):
    """
    現在ログインしているユーザーの情報を取得

    Returns:
        ユーザー情報
    """
    return UserInfo(
        user_id=current_user.user_id,
        username=current_user.username,
        avatar=None,
    )


# ============ API ルーターの登録 ============

app.include_router(classes.router)
app.include_router(exams.router)
app.include_router(makeup.router)
app.include_router(settings.router)


# ============ ヘルスチェック ============


@app.get("/health")
async def health_check():
    """
    ヘルスチェックエンドポイント

    Returns:
        ステータス
    """
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def root():
    """
    ルートページ（HTMLダッシュボード）

    Returns:
        HTMLコンテンツ
    """
    template_path = os.path.join(
        os.path.dirname(__file__), "templates", "dashboard.html"
    )
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        return """
        <html>
            <head>
                <title>授業情報Bot - Webダッシュボード</title>
            </head>
            <body>
                <h1>授業情報Bot - Webダッシュボード</h1>
                <p><a href="/docs">API ドキュメント</a></p>
            </body>
        </html>
        """


# ============ ログイン画面 ============


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """
    ログイン画面

    Returns:
        HTMLコンテンツ
    """
    template_path = os.path.join(os.path.dirname(__file__), "templates", "login.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        from web.config import DISCORD_CLIENT_ID, DISCORD_REDIRECT_URI

        return f"""
        <html>
            <head>
                <title>ログイン</title>
            </head>
            <body>
                <h1>Discord でログイン</h1>
                <a href="https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify">
                    Discord ログイン
                </a>
            </body>
        </html>
        """


# ============ エラーハンドリング ============


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """HTTP例外のハンドリング"""
    return {
        "detail": exc.detail,
        "status_code": exc.status_code,
    }


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """一般例外のハンドリング"""
    logger.exception(f"予期しないエラー: {exc}")
    return {
        "detail": "内部サーバーエラーが発生しました",
        "status_code": 500,
    }


# ============ ミドルウェアの追加設定 ============


@app.middleware("http")
async def add_security_headers(request, call_next):
    """セキュリティヘッダーの追加"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )
