"""
Gmail OAuth（PKCE）共通処理

認証URLの発行時に code_verifier をサーバー側に保持し、コード交換時に照合する。
これにより、第三者が他人の認証コードを入手しても自分のアカウントに紐付けられない。
（Google は code_verifier が一致しないコードの交換を拒否する）
"""

import os
import glob
import pickle
import time
import threading
import logging
from google_auth_oauthlib.flow import Flow  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
PENDING_TTL_SECONDS = 15 * 60
MAX_PENDING = 1000
TOKEN_DIR = os.path.join(os.getcwd(), "gmail_tokens")

# state -> {"verifier", "user_id"(None=未ログインのWeb), "expires"}
# Bot と Web サーバーは同一プロセスで動くため、メモリ上で共有できる
_pending: dict[str, dict] = {}
_lock = threading.Lock()


def get_redirect_uri() -> str:
    # .env の読み込み前に import されても反映されるよう、使用時に読む
    return os.getenv("GMAIL_REDIRECT_URI", "https://ninigi05.github.io/oauth-redirect/")


def resolve_credentials_path() -> str:
    """環境差分（ローカル/コンテナ）を吸収して Google OAuth クライアント JSON のパスを解決する。"""
    env_val = (os.getenv("GMAIL_CREDENTIALS") or "").strip()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search_roots = [os.getcwd(), script_dir, "/app"]

    if env_val:
        if os.path.isabs(env_val):
            return env_val
        for root in search_roots:
            path = os.path.join(root, env_val)
            if os.path.exists(path):
                return path

    # よくあるファイル名を自動検出する
    for root in search_roots:
        for pattern in ("client_secret*.json", "credentials.json", "gmail_credentials.json"):
            matched = sorted(glob.glob(os.path.join(root, pattern)))
            if matched:
                logger.info("[INFO] GMAIL_CREDENTIALS を自動検出: %s", matched[0])
                return matched[0]

    if env_val:
        # 後段のエラーメッセージを分かりやすくするため、スクリプト基準のパスを返す
        return os.path.join(script_dir, env_val)
    return ""


class GmailAuthError(Exception):
    """利用者に表示してよいメッセージを持つ認証エラー"""


def _cleanup_locked(now: float) -> None:
    for state in [s for s, v in _pending.items() if v["expires"] <= now]:
        del _pending[state]


def create_auth_url(credentials_file: str, user_id: int | None = None) -> tuple[str, str]:
    """認証URLと state を発行する。user_id を渡すと、その利用者しか交換できない。"""
    flow = Flow.from_client_secrets_file(
        credentials_file,
        scopes=GMAIL_SCOPES,
        redirect_uri=get_redirect_uri(),
        autogenerate_code_verifier=True,
    )
    auth_url, state = flow.authorization_url(prompt="consent", access_type="offline")
    now = time.time()
    with _lock:
        _cleanup_locked(now)
        if user_id is None and len(_pending) >= MAX_PENDING:
            # ログイン不要で発行できるため、大量発行でメモリを埋められないよう上限を設ける
            raise GmailAuthError("現在認証URLを発行できません。しばらくしてからやり直してください。")
        if user_id is not None:
            # 同じ利用者の古い発行分は無効にする（最新の URL のみ有効）
            for s in [s for s, v in _pending.items() if v["user_id"] == user_id]:
                del _pending[s]
        _pending[state] = {
            "verifier": flow.code_verifier,
            "user_id": user_id,
            "expires": now + PENDING_TTL_SECONDS,
        }
    return auth_url, state


def _take_pending(user_id: int, state: str | None) -> tuple[str, dict]:
    with _lock:
        _cleanup_locked(time.time())
        if state:
            entry = _pending.get(state)
            if entry is None or entry["user_id"] not in (None, user_id):
                raise GmailAuthError(
                    "認証の有効期限が切れたか、別のアカウントで開始された認証です。認証URLの取得からやり直してください。"
                )
        else:
            candidates = [s for s, v in _pending.items() if v["user_id"] == user_id]
            if not candidates:
                raise GmailAuthError(
                    "有効な認証がありません。/mail auth（Webでは認証URLの取得）からやり直してください。"
                )
            state = candidates[-1]
            entry = _pending[state]
        del _pending[state]
    return state, entry


def exchange_code(
    credentials_file: str, user_id: int, code: str, state: str | None = None
):
    """認証コードをトークンに交換して保存する。

    state があればその発行分、なければ user_id に紐付いた最新の発行分の code_verifier を使う。
    """
    code = (code or "").strip()
    if not code:
        raise GmailAuthError("認証コードが空です。")
    state, entry = _take_pending(user_id, state)
    flow = Flow.from_client_secrets_file(
        credentials_file,
        scopes=GMAIL_SCOPES,
        redirect_uri=get_redirect_uri(),
        state=state,
        code_verifier=entry["verifier"],
    )
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        logger.warning(f"[Gmail] トークン交換失敗 user={user_id}: {e}")
        raise GmailAuthError(
            "認証コードを確認できませんでした。認証URLの取得からやり直してください。"
        )
    os.makedirs(TOKEN_DIR, exist_ok=True)
    with open(os.path.join(TOKEN_DIR, f"user_{user_id}.pickle"), "wb") as f:
        pickle.dump(flow.credentials, f)
    return flow.credentials
