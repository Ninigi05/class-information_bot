"""
Discord OAuth2 認証処理
"""

import hashlib
import json
import os
import tempfile
import threading

import httpx
import logging
import secrets
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional, Tuple
from jose import JWTError, jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from web.config import (
    DISCORD_CLIENT_ID,
    DISCORD_CLIENT_SECRET,
    DISCORD_REDIRECT_URI,
    DISCORD_OAUTH_AUTHORIZE_URL,
    DISCORD_OAUTH_TOKEN_URL,
    DISCORD_API_USER_URL,
    SECRET_KEY,
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    BASE_DIR,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from web.schemas import UserInfo, AuthResponse, LoginUrlResponse

logger = logging.getLogger(__name__)
security = HTTPBearer()


class TokenData(BaseModel):
    """トークンペイロード"""
    user_id: int
    username: str
    exp: datetime


def generate_state() -> str:
    """CSRF対策用のランダムな文字列（state）を生成"""
    return secrets.token_urlsafe(32)


def get_login_url() -> Tuple[str, str]:
    """
    Discord OAuth2 認証 URL を生成
    
    Returns:
        (認証URL, state)
    """
    state = generate_state()
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",  # 必要最小限のスコープ
        "state": state
    }
    url = f"{DISCORD_OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    return url, state


async def exchange_discord_code(code: str) -> dict:
    """
    Discord 認可コードを交換してアクセストークンを取得
    
    Args:
        code: Discord から返された認可コード
        
    Returns:
        Discord API から返されたトークン情報
        
    Raises:
        HTTPException: コード交換に失敗した場合
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                DISCORD_OAUTH_TOKEN_URL,
                data={
                    "client_id": DISCORD_CLIENT_ID,
                    "client_secret": DISCORD_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": DISCORD_REDIRECT_URI,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Discord OAuth トークン交換失敗: {e}")
        if hasattr(e, 'response') and e.response:
            logger.error(f"レスポンス詳細: {e.response.text}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discord 認証（トークン交換）に失敗しました",
        )


async def get_discord_user(access_token: str) -> dict:
    """
    Discord アクセストークンを使用してユーザー情報を取得
    
    Args:
        access_token: Discord アクセストークン
        
    Returns:
        Discord ユーザー情報
        
    Raises:
        HTTPException: ユーザー情報取得に失敗した場合
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                DISCORD_API_USER_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Discord ユーザー情報取得失敗: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ユーザー情報の取得に失敗しました",
        )


def create_access_token(user_id: int, username: str) -> str:
    """
    JWT アクセストークンを作成
    
    Args:
        user_id: Discord ユーザーID
        username: Discord ユーザー名
        
    Returns:
        JWT トークン文字列
    """
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "user_id": user_id,
        "username": username,
        "exp": expire,
        "type": "access",
    }
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> TokenData:
    """
    JWT トークンを検証
    
    Args:
        token: JWT トークン文字列
        
    Returns:
        デコード済みのトークンデータ
        
    Raises:
        HTTPException: トークンが無効な場合
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="認証に失敗しました",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("user_id")
        username: str = payload.get("username")
        if user_id is None or username is None:
            raise credentials_exception
        token_type = payload.get("type")
        if token_type is not None and token_type != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    return TokenData(user_id=user_id, username=username, exp=datetime.utcnow())


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> TokenData:
    """
    現在のユーザー情報を取得（依存関数）
    
    Args:
        credentials: HTTP Bearer トークン
        
    Returns:
        トークンデータ（user_id, username）
        
    Raises:
        HTTPException: 認証に失敗した場合
    """
    token = credentials.credentials
    return verify_token(token)


async def authenticate_user(code: str) -> AuthResponse:
    """
    Discord 認可コードを使用してユーザーを認証
    
    Args:
        code: Discord から返された認可コード
        
    Returns:
        認証情報（トークン、ユーザー情報）
    """
    # ステップ 3: アクセストークンの交換 (バックエンド ➔ Discord API)
    discord_token_data = await exchange_discord_code(code)
    access_token = discord_token_data.get("access_token")
    
    # ステップ 4: ユーザー情報の取得 (バックエンド ➔ Discord API)
    user_data = await get_discord_user(access_token)
    user_id = int(user_data["id"])
    username = user_data.get("username", "Unknown")
    
    # アバターURLの組み立て
    avatar_hash = user_data.get("avatar")
    avatar_url = None
    if avatar_hash:
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png"
    
    # ステップ 5: セッションの確立 (JWTの生成)
    jwt_token = create_access_token(user_id, username)
    
    return AuthResponse(
        access_token=jwt_token,
        token_type="bearer",
        user=UserInfo(
            user_id=user_id,
            username=username,
            avatar=avatar_url,
        ),
    )


# =============== リフレッシュトークン（長い証） ===============
# 長い証は推測できないランダム文字列で、サーバー側にはハッシュだけを保存する。
# これにより「いつでも無効化できる」「再発行のたびに差し替える（ローテーション）」が可能になる。
REFRESH_TOKENS_FILE = os.path.join(BASE_DIR, "refresh_tokens.json")
_refresh_store_lock = threading.Lock()


def _hash_refresh_token(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def _load_refresh_store() -> dict:
    try:
        with open(REFRESH_TOKENS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_refresh_store(store: dict) -> None:
    target_dir = os.path.dirname(os.path.abspath(REFRESH_TOKENS_FILE))
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".refresh_tokens.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
        os.replace(tmp_path, REFRESH_TOKENS_FILE)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _refresh_record_expired(record: dict) -> bool:
    try:
        return datetime.fromisoformat(record.get("expires_at", "")) <= datetime.utcnow()
    except ValueError:
        return True


def issue_refresh_token(user_id: int, username: str) -> str:
    """長い証を発行して生の文字列を返す（サーバーにはハッシュだけが残る）。"""
    raw = secrets.token_urlsafe(48)
    now = datetime.utcnow()
    with _refresh_store_lock:
        store = _load_refresh_store()
        # 期限切れの記録は掃除する（使い回し検知のため、期限内なら使い回された記録も残す）
        store = {h: r for h, r in store.items() if not _refresh_record_expired(r)}
        store[_hash_refresh_token(raw)] = {
            "user_id": user_id,
            "username": username,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat(),
            "revoked": False,
            "rotated": False,
        }
        _save_refresh_store(store)
    return raw


def verify_refresh_token(raw: str) -> Optional[dict]:
    """長い証が有効ならその記録を返す。副作用はない（確認だけ）。"""
    if not raw:
        return None
    with _refresh_store_lock:
        record = _load_refresh_store().get(_hash_refresh_token(raw))
    if not record or record.get("revoked") or _refresh_record_expired(record):
        return None
    return dict(record)


def rotate_refresh_token(raw: str) -> Optional[Tuple[str, dict]]:
    """長い証を新しいものに差し替える。成功したら (新しい生トークン, 記録) を返す。
    差し替え済みの古い証が再び使われた場合は失敗させる（警告ログのみ。
    並行リクエストの競合でも起き得るため、ここでは一族全無効化まではしない）。"""
    if not raw:
        return None
    with _refresh_store_lock:
        store = _load_refresh_store()
        record = store.get(_hash_refresh_token(raw))
        if not record or _refresh_record_expired(record):
            return None
        if record.get("revoked"):
            if record.get("rotated"):
                logger.warning(
                    f"[Web Auth] 差し替え済みリフレッシュトークンの再使用を検知: user={record.get('user_id')}"
                )
            return None
        record["revoked"] = True
        record["rotated"] = True
        new_raw = secrets.token_urlsafe(48)
        now = datetime.utcnow()
        new_record = {
            "user_id": record["user_id"],
            "username": record["username"],
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat(),
            "revoked": False,
            "rotated": False,
        }
        store[_hash_refresh_token(new_raw)] = new_record
        _save_refresh_store(store)
        return new_raw, dict(new_record)


def revoke_refresh_token(raw: str) -> bool:
    """長い証を無効化する（ログアウト用）。無効化できたら True。"""
    if not raw:
        return False
    with _refresh_store_lock:
        store = _load_refresh_store()
        record = store.get(_hash_refresh_token(raw))
        if not record or record.get("revoked"):
            return False
        record["revoked"] = True
        _save_refresh_store(store)
        return True
