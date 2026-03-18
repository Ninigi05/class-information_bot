"""
API セキュリティ（API_KEY 認証）
"""

import os
import logging
from fastapi import HTTPException, status, Header
from typing import Optional

logger = logging.getLogger(__name__)

# 環境変数から API_KEY を取得
API_KEY = os.getenv("API_KEY", "default-api-key-change-in-production")
API_KEY_HEADER = "X-API-Key"


def verify_api_key(x_api_key: Optional[str] = Header(None)) -> bool:
    """
    リクエストヘッダーの API_KEY を検証

    Args:
        x_api_key: リクエストヘッダーの X-API-Key 値

    Returns:
        True: 有効

    Raises:
        HTTPException: API_KEY が無効な場合
    """
    if not x_api_key:
        logger.warning("API_KEY が提供されていません")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API_KEY が必要です（X-API-Key ヘッダー）",
        )

    if x_api_key != API_KEY:
        logger.warning(f"無効な API_KEY が使用されました: {x_api_key[:10]}***")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API_KEY が無効です",
        )

    return True


def get_api_key_from_env() -> str:
    """
    環境変数から API_KEY を取得（またはランダムに生成）

    Returns:
        API_KEY 文字列
    """
    api_key = os.getenv("API_KEY")
    if not api_key:
        # ランダムなAPIキーを生成
        import secrets

        api_key = secrets.token_urlsafe(32)
        logger.warning(f"API_KEY が設定されていません。自動生成しました: {api_key}")
    return api_key
