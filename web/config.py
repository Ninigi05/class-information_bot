"""
設定ファイル（環境変数等）
"""

import os
from dotenv import load_dotenv

load_dotenv()

# =============== Discord OAuth2 ===============
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")

# TUNNEL_PUBLIC_BASE_URL があればそれをベースに自動で組み立てる
public_base_url = os.getenv("TUNNEL_PUBLIC_BASE_URL", "").strip().rstrip("/")
if public_base_url:
    default_redirect = f"{public_base_url}/auth/callback"
else:
    default_redirect = "http://localhost:8000/auth/callback"

DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", default_redirect)

# Discord OAuth2 エンドポイント
DISCORD_OAUTH_AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_OAUTH_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API_USER_URL = "https://discord.com/api/users/@me"

# =============== FastAPI ===============
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24時間

# =============== データ保存先 ===============
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# =============== CORS ===============
ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://localhost:3000",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:3000",
]

# =============== ロギング ===============
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# =============== その他 ===============
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
