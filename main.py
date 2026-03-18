import os
import base64
import traceback
import logging
from logging.handlers import RotatingFileHandler
import discord
import unicodedata
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime
import pickle
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow

from utils import load_user_data, save_user_data, send_dm, send_long_dm
from web_api_integration import start_web_server

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID") or 0)
GMAIL_CREDENTIALS = os.path.join("/app", os.getenv("GMAIL_CREDENTIALS") or "")

BASE_DIR = os.getcwd()


def configure_logging():
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "bot.log")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if root_logger.handlers:
        root_logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)


configure_logging()
logger = logging.getLogger(__name__)

# Gmail 認証フロー（一時保存）
user_auth_flows = {}


# --- Gmailサービス取得 ---
def get_gmail_service(user_id):
    token_dir = os.path.join(BASE_DIR, "gmail_tokens")
    os.makedirs(token_dir, exist_ok=True)
    token_file = os.path.join(token_dir, f"user_{user_id}.pickle")
    creds = None

    if os.path.exists(token_file):
        try:
            with open(token_file, "rb") as f:
                creds = pickle.load(f)
        except Exception as e:
            logger.warning(
                f"[WARNING] token ファイル読み込み失敗または破損 (user {user_id}): {e}"
            )
            try:
                bad_backup = token_file + ".bak"
                os.replace(token_file, bad_backup)
                logger.info(
                    f"[INFO] 不正な token を {bad_backup} に移動しました。/mail auth で再認証してください。"
                )
            except Exception:
                pass
            return None

    if creds is None or not hasattr(creds, "valid"):
        logger.info(f"Gmail認証が必要です（creds 不正/未設定）: user {user_id}")
        return None

    try:
        if not creds.valid:
            if getattr(creds, "expired", False) and getattr(
                creds, "refresh_token", None
            ):
                try:
                    creds.refresh(Request())
                    with open(token_file, "wb") as f:
                        pickle.dump(creds, f)
                    logger.info(
                        f"[INFO] トークンをリフレッシュしました: user {user_id}"
                    )
                except Exception as e:
                    logger.warning(f"[WARNING] トークンリフレッシュ失敗: {e}")
                    return None
            else:
                logger.warning(f"トークン無効またはリフレッシュ不可: user {user_id}")
                return None
    except Exception as e:
        logger.exception(f"[ERROR] creds チェック/リフレッシュ中に例外: {e}")
        return None

    try:
        service = build("gmail", "v1", credentials=creds)
        return service
    except Exception as e:
        logger.exception(f"[ERROR] Gmail service 作成失敗: {e}")
        return None


# --- Gmail payload 再帰探索ヘルパー ---
def _get_text_from_part(part):
    if not part:
        return ""
    body = part.get("body", {}) or {}
    data = body.get("data")
    if data:
        try:
            pad = "=" * (-len(data) % 4)
            decoded = base64.urlsafe_b64decode(data + pad).decode(
                "utf-8", errors="replace"
            )
            mime = (part.get("mimeType") or "").lower()
            if "html" in mime:
                text = re.sub(r"<[^>]+>", "", decoded)
                text = re.sub(r"\s+", " ", text).strip()
                return text
            return decoded
        except Exception:
            try:
                return base64.b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                return ""

    mime = (part.get("mimeType") or "").lower()
    if "html" in mime and "body" in part:
        html_data = part.get("body", {}).get("data")
        if html_data:
            try:
                pad = "=" * (-len(html_data) % 4)
                decoded = base64.urlsafe_b64decode(html_data + pad).decode(
                    "utf-8", errors="replace"
                )
                text = re.sub(r"<[^>]+>", "", decoded)
                return text
            except Exception:
                pass

    for sub in part.get("parts", []) or []:
        text = _get_text_from_part(sub)
        if text:
            return text
    return ""


def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"（.*?）|\(.*?\)|\[.*?\]|【.*?】", "", s)
    s = re.sub(r"[ \t\n\r\-\—\–\_\/\\\:\;，．,。・、·•'\"「」『』<>]", "", s)
    s = s.lower()
    return s.strip()


def extract_numbers(s: str) -> list:
    if not s:
        return []
    nums = re.findall(r"\d+", s)
    return [int(n) for n in nums] if nums else []


def remove_common_suffixes(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"(概論|演習|基礎|実験|実習|総合|講義|入門|Ⅰ|Ⅱ|Ⅲ|ⅠⅠ|ⅡⅠ)$", "", s)


def subjects_match_strict(
    parsed_subject_raw: str, cls_subject_raw: str, parsed_periods: list, cls_period_raw
) -> bool:
    a_raw = (parsed_subject_raw or "").strip()
    b_raw = (cls_subject_raw or "").strip()

    if parsed_periods:
        try:
            cls_p_int = int(str(cls_period_raw))
        except Exception:
            cls_p_int = None
        if cls_p_int is None or cls_p_int not in parsed_periods:
            return False

    a = normalize_text(a_raw)
    b = normalize_text(b_raw)
    if a and b and a == b:
        return True

    a_stem = remove_common_suffixes(a)
    b_stem = remove_common_suffixes(b)
    if a_stem and b_stem and a_stem == b_stem:
        a_nums = extract_numbers(a_raw)
        b_nums = extract_numbers(b_raw)
        if not a_nums and not b_nums:
            return True
        if a_nums == b_nums and a_nums:
            return True
        return False

    return False


def parse_cancellation_email(mail):
    subject_header = (mail.get("subject") or "").strip()
    body = (mail.get("body") or "").strip()

    date_match = re.search(
        r"休講日\s*[：:]\s*(?:(令和\s*(\d+)年)|(\d{4})年)?\s*(\d{1,2})月\s*(\d{1,2})日",
        body,
    )
    if not date_match:
        date_match2 = re.search(r"(\d{1,2})月\s*(\d{1,2})日", subject_header)
        if date_match2:
            month = int(date_match2.group(1))
            day = int(date_match2.group(2))
            year = datetime.now().year
            date_str = f"{year}-{month:02d}-{day:02d}"
        else:
            logger.warning(f"[WARNING] 日付抽出失敗: {subject_header}")
            return None
    else:
        reiwa_num = date_match.group(2)
        western_year = date_match.group(3)
        month = int(date_match.group(4))
        day = int(date_match.group(5))
        if reiwa_num:
            year = 2018 + int(reiwa_num)
        elif western_year:
            year = int(western_year)
        else:
            year = datetime.now().year
        date_str = f"{year}-{month:02d}-{day:02d}"

    periods = []
    period_block = re.search(
        r"時\s*限\s*[：:]\s*([0-9]{1,2}(?:\s*[・,、/]\s*[0-9]{1,2})*)", body
    )
    if period_block:
        raw = period_block.group(1)
        parts = re.split(r"[・,、/]\s*", raw)
        for p in parts:
            try:
                periods.append(int(p))
            except Exception:
                continue
    else:
        nums = re.findall(r"(\d{1,2})\s*限", body)
        if nums:
            periods = [int(n) for n in nums]

    period_display = None
    if periods:
        periods = sorted(list(set(periods)))
        period_display = "・".join(str(p) for p in periods)

    course_name = None
    subj_m = re.search(r"科目名\s*[：:]\s*(.+)", body)
    if subj_m:
        course_name = subj_m.group(1).splitlines()[0].strip()
    else:
        m2 = re.search(r"[『「](.+?)[』」]", subject_header)
        if m2:
            course_name = m2.group(1).strip()
        else:
            cleaned = re.sub(
                r"休講通知|休講|通知|Fwd:|FW:|-|：|:", "", subject_header, flags=re.I
            ).strip()
            if cleaned:
                course_name = cleaned

    return {
        "date": date_str,
        "period": period_display,
        "periods": periods,
        "subject": course_name,
        "body": body,
        "subject_header": subject_header,
    }


def fetch_cancellation_emails(user_id):
    try:
        service = get_gmail_service(user_id)
        if not service:
            logger.info(f"Gmail認証が必要です（fetchスキップ）: user {user_id}")
            return []

        results = (
            service.users()
            .messages()
            .list(userId="me", q="subject:休講 newer_than:14d", maxResults=50)
            .execute()
        )
        messages = results.get("messages", [])
        raw_list = []

        for msg in messages:
            m = (
                service.users()
                .messages()
                .get(userId="me", id=msg["id"], format="full")
                .execute()
            )
            payload = m.get("payload", {})
            headers = payload.get("headers", [])
            subject_hdr = next(
                (h["value"] for h in headers if h.get("name") == "Subject"), ""
            )

            def extract_text_from_parts(parts):
                for part in parts or []:
                    mime = part.get("mimeType", "")
                    if mime == "text/plain" and part.get("body", {}).get("data"):
                        try:
                            return base64.urlsafe_b64decode(
                                part["body"]["data"]
                            ).decode("utf-8", errors="ignore")
                        except Exception:
                            return ""
                    if mime.startswith("multipart") and part.get("parts"):
                        txt = extract_text_from_parts(part.get("parts"))
                        if txt:
                            return txt
                return ""

            body_text = extract_text_from_parts(payload.get("parts", []))
            if not body_text:
                top_body = payload.get("body", {}).get("data")
                if top_body:
                    try:
                        body_text = base64.urlsafe_b64decode(top_body).decode(
                            "utf-8", errors="ignore"
                        )
                    except Exception:
                        body_text = ""
            if not body_text:
                body_text = m.get("snippet", "")

            raw_list.append({"subject": subject_hdr, "body": body_text})

        user_data = load_user_data(user_id)
        classes = user_data.get("classes", [])

        matches = []
        for raw in raw_list:
            parsed = parse_cancellation_email(raw)
            if not parsed:
                continue

            try:
                parsed_date = datetime.fromisoformat(parsed["date"]).date()
                parsed_wd = parsed_date.weekday()
            except Exception:
                continue

            parsed_subject = parsed.get("subject") or parsed.get("course") or ""
            parsed_periods = parsed.get("periods") or []
            if not parsed_periods:
                p = parsed.get("period") or parsed.get("period_raw") or ""
                if p:
                    parsed_periods = extract_numbers(str(p))

            found = False
            for cls in classes:
                try:
                    cls_day = int(cls.get("day"))
                except Exception:
                    continue
                if cls_day != parsed_wd:
                    continue

                cls_subject = cls.get("subject", "") or ""
                cls_period = cls.get("period")

                if parsed_subject:
                    ok = subjects_match_strict(
                        parsed_subject, cls_subject, parsed_periods, cls_period
                    )
                    if ok:
                        found = True
                        break
                else:
                    try:
                        cls_p_int = int(str(cls_period))
                    except Exception:
                        cls_p_int = None
                    if cls_p_int and parsed_periods and cls_p_int in parsed_periods:
                        found = True
                        break

            if found:
                matches.append(
                    {
                        "date": parsed["date"],
                        "period": parsed.get("period"),
                        "periods": parsed.get("periods") or parsed_periods,
                        "subject": parsed_subject or raw.get("subject"),
                        "body": parsed.get("body") or raw.get("body"),
                    }
                )
            else:
                logger.info(
                    f"[DEBUG-no-match] user={user_id} 未一致: parsed_subject='{parsed_subject}' date={parsed['date']}"
                )

        return matches

    except Exception as e:
        logger.exception(f"Gmail休講情報の取得に失敗しました: {e}")
        return []


# --- mail グループ ---
mail_group = app_commands.Group(
    name="mail", description="Gmail 認証および休講取得用コマンド"
)


@mail_group.command(
    name="auth", description="Gmail 認証フローを開始します（DMでURL送付）"
)
async def mail_auth(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = interaction.user.id
    try:
        flow = Flow.from_client_secrets_file(
            GMAIL_CREDENTIALS,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            redirect_uri="https://ninigi05.github.io/oauth-redirect/",
        )
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        user_auth_flows[user_id] = flow
        await send_dm(
            interaction.user,
            f"Gmail認証を開始します。\n以下のURLを開き、表示された認証コード（code）をコピーしてください：\n\n{auth_url}"
            f"\n\nコピーしたコードは `/mail setcode <認証コード>` で入力してください。",
        )
        await interaction.followup.send("認証URLをDMに送信しました。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"認証開始に失敗しました: {e}", ephemeral=True)


@mail_group.command(
    name="setcode", description="Gmail 認証コードを入力して連携を完了します"
)
@app_commands.describe(code="認証コード")
async def mail_setcode(interaction: discord.Interaction, code: str):
    await interaction.response.defer(ephemeral=True)
    user_id = interaction.user.id
    if user_id not in user_auth_flows:
        await interaction.followup.send(
            "先に /mail auth を実行してください。", ephemeral=True
        )
        return
    try:
        flow = user_auth_flows[user_id]
        flow.fetch_token(code=code)
        creds = flow.credentials
        token_dir = os.path.join(BASE_DIR, "gmail_tokens")
        os.makedirs(token_dir, exist_ok=True)
        token_file = os.path.join(token_dir, f"user_{user_id}.pickle")
        with open(token_file, "wb") as token:
            pickle.dump(creds, token)
        del user_auth_flows[user_id]
        await send_dm(interaction.user, "Gmail 認証が完了しました。")
        await interaction.followup.send("認証完了しました。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"認証に失敗しました: {e}", ephemeral=True)


@mail_group.command(
    name="fetch",
    description="Gmailから最新の休講情報を取得してDMで表示（保存はしません）",
)
async def mail_fetch(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = interaction.user.id
    try:
        cancellations = fetch_cancellation_emails(user_id)
        if not cancellations:
            await send_dm(
                interaction.user, "登録授業に該当する休講情報は見つかりませんでした。"
            )
            await interaction.followup.send("取得完了（該当なし）。", ephemeral=True)
            return
        lines = ["最新の休講情報（表示のみ）:"]
        for c in cancellations:
            date_display = c.get("date") or "不明日付"
            period_display = c.get("period") or "?"
            subj = c.get("subject") or c.get("subject_header") or "（不明）"
            lines.append(f"{date_display} {period_display}限 {subj}")
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send("休講情報をDMで送信しました。", ephemeral=True)
    except Exception as e:
        logger.exception(f"[ERROR] mail_fetch error: {e}")
        await interaction.followup.send(
            f"休講情報の取得に失敗しました: {e}", ephemeral=True
        )


# --- Bot クラス ---
class ClassBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # cog のロード
        await self.load_extension("cogs.class_cog")
        await self.load_extension("cogs.exam_cog")
        await self.load_extension("cogs.setting_cog")
        await self.load_extension("cogs.notification_cog")

        # mail グループ（Gmail認証・取得）
        self.tree.add_command(mail_group)

        # /help コマンド
        @self.tree.command(
            name="help",
            description="使い方ヘルプをDMで受け取ります（コマンド一覧をカテゴリ別に表示）",
        )
        async def help_command(interaction: discord.Interaction):
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

            lines = []
            lines.append("**授業情報Bot — ヘルプ（カテゴリ別）**\n")
            lines.append(
                "※このヘルプはDMで届きます。DMを許可していない場合は許可してください。\n"
            )

            lines.append("===  授業管理（/class） ===")
            lines.append(
                "• /class add weekday period subject room\n  → 授業を登録します"
            )
            lines.append("• /class remove weekday period\n  → 授業を削除します")
            lines.append("• /class list\n  → 登録授業一覧をDMで受け取ります")
            lines.append("• /class table\n  → 時間割表形式で表示します")
            lines.append(
                "• /class setroom date period new_room\n  → 指定日の教室を変更します"
            )
            lines.append(
                "• /class setday date new_weekday\n  → 指定日の曜日を変更します（代替時間割対応）\n"
            )

            lines.append("===  補講・休講管理 ===")
            lines.append("• /makeup add date time subject room\n  → 補講を追加します")
            lines.append("• /makeup remove date time\n  → 補講を削除します")
            lines.append("• /makeup list\n  → 補講一覧を表示します")
            lines.append("• /cancel add date subject\n  → 手動で休講を追加します")
            lines.append("• /cancel remove date subject\n  → 手動休講を削除します")
            lines.append("• /cancel list\n  → 手動休講一覧を表示します\n")

            lines.append("===  試験時間割（/exam） ===")
            lines.append("• /exam create name start end\n  → 試験用時間割を作成します")
            lines.append("• /exam list\n  → 登録済み試験時間割を一覧表示します")
            lines.append("• /exam show name\n  → 指定時間割の中身を表示します")
            lines.append(
                "• /exam addclass name weekday period subject room [time]\n  → 試験時間割へ授業を追加します"
            )
            lines.append(
                "• /exam removeclass name weekday period\n  → 試験時間割から授業を削除します"
            )
            lines.append("• /exam delete name\n  → 試験時間割を削除します")
            lines.append(
                "• /exam set_time period time\n  → 試験期間中の時限開始時刻を設定します\n"
            )

            lines.append("===  通知設定（/notify） ===")
            lines.append(
                "• /notify set type first second\n  → 通知タイミングを設定します（type: normal または exam）"
            )
            lines.append("• /notify show\n  → 現在の通知設定を表示します")
            lines.append(
                "• /notify set_period period time\n  → 時限ごとの開始時刻を設定します\n"
            )

            lines.append("===  時限・設定（/setting） ===")
            lines.append(
                "• /setting period_time period time\n  → 時限の開始時刻をカスタム設定します"
            )
            lines.append("• /setting show\n  → 現在の設定を表示します")
            lines.append(
                "• /setting reset_period period\n  → 時限設定をデフォルトに戻します\n"
            )

            lines.append("===  Gmail 認証 / 連携（/mail） ===")
            lines.append(
                "• /mail auth\n  → Gmail 認証を開始します（認証URLをDMで送信）"
            )
            lines.append(
                "• /mail setcode code\n  → 認証コードを入力して連携を完了します"
            )
            lines.append(
                "• /mail fetch\n  → Gmailから最新の休講情報を取得して保存します\n"
            )

            lines.append("===  注意事項 ===")
            lines.append("• 長い出力は複数メッセージに分割してDMで送信します。")
            lines.append(
                "• Gmail 関連機能を使うには /mail auth → /mail setcode の順で認証してください。"
            )

            help_text = "\n".join(lines)
            try:
                await send_long_dm(interaction.user, help_text)
                try:
                    await interaction.followup.send(
                        "ヘルプをDMで送信しました。DMを確認してください。",
                        ephemeral=True,
                    )
                except Exception:
                    pass
            except discord.Forbidden:
                try:
                    await interaction.followup.send(
                        "DM にヘルプを送信できませんでした。DMを許可しているか確認してください。",
                        ephemeral=True,
                    )
                except Exception:
                    pass
            except Exception as e:
                logger.exception(f"[ERROR] help_command DM 送信失敗: {e}")
                try:
                    await interaction.followup.send(
                        "ヘルプ送信中にエラーが発生しました。", ephemeral=True
                    )
                except Exception:
                    pass


bot = ClassBot()


@bot.event
async def on_ready():
    logger.info(f"Bot 起動完了: {bot.user}")
    guild = discord.Object(id=GUILD_ID) if GUILD_ID else None
    try:
        if guild:
            synced_guild = await bot.tree.sync(guild=guild)
            logger.info(f"ギルドにコマンド同期: {[cmd.name for cmd in synced_guild]}")
        else:
            logger.info("GUILD_ID が設定されていません。ギルド同期をスキップします。")
    except Exception as e:
        logger.exception(f"ギルド同期失敗: {e}")
    try:
        synced_global = await bot.tree.sync()
        logger.info(f"グローバルにコマンド同期: {len(synced_global)} 件")
    except Exception as e:
        logger.exception(f"グローバル同期失敗: {e}")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN が未設定です。.env を確認してください。")

    # Web API サーバーをスレッドで起動（Discord Bot と並行実行）
    web_port = int(os.getenv("WEB_PORT", "8000"))
    web_host = os.getenv("WEB_HOST", "0.0.0.0")
    logger.info(f"Web API サーバーを起動します: {web_host}:{web_port}")

    try:
        start_web_server(host=web_host, port=web_port)
        logger.info("[INFO] Web API サーバーが起動しました")
    except Exception as e:
        logger.exception(f"[ERROR] Web API サーバー起動失敗: {e}")

    # Discord Bot を起動
    logger.info("[INFO] Discord Bot を起動します...")
    bot.run(DISCORD_TOKEN)
