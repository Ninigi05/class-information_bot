"""
Discord OAuth2 認証処理
"""

import httpx
import logging
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthCredentials
from pydantic import BaseModel

from web.config import (
    DISCORD_CLIENT_ID,
    DISCORD_CLIENT_SECRET,
    DISCORD_REDIRECT_URI,
    DISCORD_OAUTH_TOKEN_URL,
    DISCORD_API_USER_URL,
    SECRET_KEY,
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from web.schemas import UserInfo, AuthResponse

logger = logging.getLogger(__name__)
security = HTTPBearer()


class TokenData(BaseModel):
    """トークンペイロード"""
    user_id: int
    username: str
    exp: datetime


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
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Discord OAuth トークン交換失敗: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discord 認証に失敗しました",
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
    except JWTError:
        raise credentials_exception
    
    return TokenData(user_id=user_id, username=username, exp=datetime.utcnow())


async def get_current_user(credentials: HTTPAuthCredentials = Depends(security)) -> TokenData:
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
    # ステップ 1: 認可コードをアクセストークンに交換
    discord_token_data = await exchange_discord_code(code)
    access_token = discord_token_data.get("access_token")
    
    # ステップ 2: アクセストークンを使用してユーザー情報を取得
    user_data = await get_discord_user(access_token)
    user_id = int(user_data["id"])
    username = user_data.get("username", "Unknown")
    avatar_url = user_data.get("avatar")
    
    # ステップ 3: JWT トークンを作成
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
