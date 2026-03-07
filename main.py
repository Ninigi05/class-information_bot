import os
import base64
import traceback
import discord
import difflib
import unicodedata
from discord.ext import tasks
from discord import app_commands
from dotenv import load_dotenv
from datetime import datetime, timedelta
import asyncio
import json
import pickle
import re
import sys
import pandas as pd
import matplotlib.pyplot as plt
import japanize_matplotlib

sys.stdout.reconfigure(encoding="utf-8")

from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from email.utils import parsedate_to_datetime

# 再実行ガード（in-memory）
LAST_NOTIFICATION_MINUTE = None
user_auth_flows = {}

load_dotenv()


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID") or 0)

# Docker コンテナ内では作業ディレクトリを /app にしている想定
GMAIL_CREDENTIALS = os.path.join("/app", os.getenv("GMAIL_CREDENTIALS") or "")
# --- Discord Bot 設定 ---
intents = discord.Intents.default()
intents.message_content = True

class ClassBot(commands.Bot):
    async def setup_hook(self):
        extensions = [
            "cogs.class_cog",
            "cogs.exam_cog",
            "cogs.setting_cog",
            "cogs.notification_cog",
        ]
        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"[INFO] {ext} をロードしました")
            except Exception as e:
                print(f"[ERROR] {ext} のロードに失敗: {e}")
        self.tree.add_command(mail_group)

bot = ClassBot(command_prefix="!", intents=intents)
tree = bot.tree

WEEKDAYS = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]
WEEKDAY_MAP = {w: i for i, w in enumerate(WEEKDAYS)}

PERIOD_TO_TIME = {
    "1": "09:00",
    "2": "10:45",
    "3": "13:15",
    "4": "15:00",
    "5": "16:45",
    "6": "18:25"
}

# デフォルト通知（ユーザー未設定時）
DEFAULT_NOTIFY = {
    "normal": {"first": 15, "second": 10},
    "exam": {"first": 30, "second": 25}
}

# --- Docker/Koyeb用 --- 
BASE_DIR = os.getcwd()  # コンテナ内のカレントディレクトリ

def user_file(user_id):
    return os.path.join(BASE_DIR, f"user_{user_id}.json")

def load_user_data(user_id):
    path = user_file(user_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[ERROR] user file read failed ({user_id}): {e}")
            return {"classes": []}
    return {"classes": []}





def save_user_data(user_id, data):
    path = user_file(user_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# --- Gmailサービス取得（堅牢化） ---
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
            print(f"[WARNING] token ファイル読み込み失敗または破損 (user {user_id}): {e}")
            try:
                bad_backup = token_file + ".bak"
                os.replace(token_file, bad_backup)
                print(f"[INFO] 不正な token を {bad_backup} に移動しました。/authmail で再認証してください。")
            except Exception:
                pass
            return None

    if creds is None or not hasattr(creds, "valid"):
        print(f" Gmail認証が必要です（creds 不正/未設定）: user {user_id}")
        return None

    try:
        if not creds.valid:
            if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
                try:
                    creds.refresh(Request())
                    with open(token_file, "wb") as f:
                        pickle.dump(creds, f)
                    print(f"[INFO] トークンをリフレッシュしました: user {user_id}")
                except Exception as e:
                    print(f"[WARNING] トークンリフレッシュ失敗: {e}")
                    return None
            else:
                print(f" トークン無効またはリフレッシュ不可: user {user_id}")
                return None
    except Exception as e:
        print(f"[ERROR] creds チェック/リフレッシュ中に例外: {e}")
        traceback.print_exc()
        return None

    try:
        service = build("gmail", "v1", credentials=creds)
        return service
    except Exception as e:
        print(f"[ERROR] Gmail service 作成失敗: {e}")
        traceback.print_exc()
        return None

# --- Gmail payload 再帰探索ヘルパー（html 対応） ---
def _get_text_from_part(part):
    if not part:
        return ""
    body = part.get("body", {}) or {}
    data = body.get("data")
    if data:
        try:
            pad = '=' * (-len(data) % 4)
            decoded = base64.urlsafe_b64decode(data + pad).decode('utf-8', errors='replace')
            mime = (part.get("mimeType") or "").lower()
            if "html" in mime:
                # タグ除去（シンプル）
                text = re.sub(r'<[^>]+>', '', decoded)
                text = re.sub(r'\s+', ' ', text).strip()
                return text
            return decoded
        except Exception:
            try:
                return base64.b64decode(data).decode('utf-8', errors='replace')
            except Exception:
                return ""

    mime = (part.get("mimeType") or "").lower()
    if "html" in mime and "body" in part:
        html_data = part.get("body", {}).get("data")
        if html_data:
            try:
                pad = '=' * (-len(html_data) % 4)
                decoded = base64.urlsafe_b64decode(html_data + pad).decode('utf-8', errors='replace')
                text = re.sub(r'<[^>]+>', '', decoded)
                return text
            except Exception:
                pass

    for sub in part.get("parts", []) or []:
        text = _get_text_from_part(sub)
        if text:
            return text
    return ""

def normalize_text(s: str) -> str:
    """比較用に正規化：NFKC、括弧内削除、空白と一般的記号を除去、小文字化（英字）。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"（.*?）|\(.*?\)|\[.*?\]|【.*?】", "", s)
    s = re.sub(r"[ \t\n\r\-\—\–\_\/\\\:\;，．,。・、·•'\"「」『』<>]", "", s)
    s = s.lower()
    return s.strip()

def fuzzy_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()

# ---------- 持ち物関連ヘルパー ----------
def parse_materials_field(field):
    """
    授業エントリの 'materials' フィールドを正規化してリストで返す。
    - 既にリストならそのまま（要素トリム）
    - 文字列ならカンマ/スペース/改行等で分割
    - None -> []
    """
    if not field:
        return []
    if isinstance(field, list):
        return [str(x).strip() for x in field if str(x).strip()]
    s = str(field)
    # 分割パターン：カンマ・全角カンマ・スラッシュ・改行・セミコロンなど
    parts = re.split(r"[,\n\r\u3001\/;；、]+", s)
    return [p.strip() for p in parts if p.strip()]

def _normalize_material_key(s: str) -> str:
    """持ち物の重複判定キー（簡易）: NFKC ノーマライズ + 小文字 + 空白除去"""
    if not s:
        return ""
    t = unicodedata.normalize("NFKC", s)
    t = re.sub(r"\s+", "", t)
    return t.lower()

def aggregate_materials_for_classes(class_list):
    """
    class_list: list of class dicts （各要素は subject, period, materials, ... など）
    戻り値: ordered list of dict:
      [
        {"key": key, "label": first_seen_label, "count": n, "classes": [subject strings]},
        ...
      ]
    重複はキーでまとめ、最初に見つかった表記を表示名(label)として使います。
    """
    mapd = {}
    order = []
    for cls in class_list:
        subj = cls.get("subject", "（無名）")
        mats = parse_materials_field(cls.get("materials"))
        for m in mats:
            key = _normalize_material_key(m)
            if not key:
                continue
            if key not in mapd:
                mapd[key] = {"key": key, "label": m.strip(), "count": 0, "classes": set()}
                order.append(key)
            mapd[key]["count"] += 1
            mapd[key]["classes"].add(subj)
    # convert classes set to list and preserve order
    result = []
    for k in order:
        entry = mapd[k]
        entry["classes"] = sorted(list(entry["classes"]))
        result.append(entry)
    return result

# --- 改良版 parse_cancellation_email ---
def parse_cancellation_email(mail):
    subject_header = (mail.get("subject") or "").strip()
    body = (mail.get("body") or "").strip()

    date_match = re.search(
        r"休講日\s*[：:]\s*(?:(令和\s*(\d+)年)|(\d{4})年)?\s*(\d{1,2})月\s*(\d{1,2})日",
        body
    )
    if not date_match:
        date_match2 = re.search(r"(\d{1,2})月\s*(\d{1,2})日", subject_header)
        if date_match2:
            month = int(date_match2.group(1))
            day = int(date_match2.group(2))
            year = datetime.now().year
            date_str = f"{year}-{month:02d}-{day:02d}"
        else:
            print(f"[WARNING] 日付抽出失敗: {subject_header}")
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
    period_block = re.search(r"時\s*限\s*[：:]\s*([0-9]{1,2}(?:\s*[・,、/]\s*[0-9]{1,2})*)", body)
    if period_block:
        raw = period_block.group(1)
        parts = re.split(r"[・,、/]\s*", raw)
        for p in parts:
            try:
                periods.append(int(p))
            except:
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
            cleaned = re.sub(r"休講通知|休講|通知|Fwd:|FW:|-|：|:","", subject_header, flags=re.I).strip()
            if cleaned:
                course_name = cleaned

    return {
        "date": date_str,
        "period": period_display,
        "periods": periods,
        "subject": course_name,
        "body": body,
        "subject_header": subject_header
    }

# --- 比較ユーティリティ ---
MATCH_THRESHOLD = 1.0

def extract_numbers(s: str) -> list:
    if not s:
        return []
    nums = re.findall(r"\d+", s)
    return [int(n) for n in nums] if nums else []

def remove_common_suffixes(s: str) -> str:
    if not s:
        return ""
    s2 = re.sub(r"(概論|演習|基礎|実験|実習|総合|実験|演習|講義|入門|Ⅰ|Ⅱ|Ⅲ|ⅠⅠ|ⅡⅠ)$", "", s)
    return s2

def subjects_match_strict(parsed_subject_raw: str, cls_subject_raw: str,
                          parsed_periods: list, cls_period_raw) -> bool:
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

    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    print(f"[DEBUG-match-check] parsed='{parsed_subject_raw}' registered='{cls_subject_raw}' ratio={ratio:.2f}")
    if ratio >= MATCH_THRESHOLD and MATCH_THRESHOLD < 1.0:
        return True

    return False

# --- 改良版 fetch_cancellation_emails（元の実装を置き換える） ---
def fetch_cancellation_emails(user_id):
    try:
        service = get_gmail_service(user_id)
        if not service:
            print(f" Gmail認証が必要です（fetchスキップ）: user {user_id}")
            return []

        results = service.users().messages().list(
            userId='me',
            q='subject:休講 newer_than:14d',
            maxResults=50
        ).execute()
        messages = results.get('messages', [])
        raw_list = []

        for msg in messages:
            m = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            payload = m.get('payload', {})
            headers = payload.get('headers', [])
            subject_hdr = next((h['value'] for h in headers if h.get('name') == 'Subject'), "")

            def extract_text_from_parts(parts):
                for part in parts or []:
                    mime = part.get('mimeType', '')
                    if mime == 'text/plain' and part.get('body', {}).get('data'):
                        try:
                            return base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
                        except:
                            return ""
                    if mime.startswith('multipart') and part.get('parts'):
                        txt = extract_text_from_parts(part.get('parts'))
                        if txt:
                            return txt
                return ""

            body_text = extract_text_from_parts(payload.get('parts', []))
            if not body_text:
                top_body = payload.get('body', {}).get('data')
                if top_body:
                    try:
                        body_text = base64.urlsafe_b64decode(top_body).decode('utf-8', errors='ignore')
                    except:
                        body_text = ""
            if not body_text:
                body_text = m.get('snippet', '')

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
                except:
                    continue
                if cls_day != parsed_wd:
                    continue

                cls_subject = cls.get("subject", "") or ""
                cls_period = cls.get("period")

                if parsed_subject:
                    ok = subjects_match_strict(parsed_subject, cls_subject, parsed_periods, cls_period)
                    ratio_dbg = difflib.SequenceMatcher(None, normalize_text(parsed_subject), normalize_text(cls_subject)).ratio()
                    
                    if ok:
                        found = True
                        break
                else:
                    try:
                        cls_p_int = int(str(cls_period))
                    except:
                        cls_p_int = None
                    if cls_p_int and parsed_periods and cls_p_int in parsed_periods:
                        print(f"[DEBUG-match] user={user_id} matched by period: {cls_p_int} in {parsed_periods}")
                        found = True
                        break

            if found:
                matches.append({
                    "date": parsed["date"],
                    "period": parsed.get("period"),
                    "periods": parsed.get("periods") or parsed_periods,
                    "subject": parsed_subject or raw.get("subject"),
                    "body": parsed.get("body") or raw.get("body")
                })
            else:
                print(f"[DEBUG-no-match] user={user_id} 未一致: parsed_subject='{parsed_subject}' date={parsed['date']}")

        return matches

    except Exception as e:
        print(f" Gmail休講情報の取得に失敗しました: {e}")
        return []

# --- DM functions ---
async def send_dm(user, message):
    try:
        dm = await user.create_dm()
        await dm.send(message)
    except Exception as e:
        try:
            uid = user.id
        except Exception:
            uid = "unknown"
        print(f"DM送信失敗: {uid} ({e})")

async def send_long_dm(user, text, chunk_size=1900):
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    try:
        dm = await user.create_dm()
        for chunk in chunks:
            await dm.send(chunk)
    except Exception as e:
        try:
            uid = user.id
        except Exception:
            uid = "unknown"
        print(f"DM送信エラー: {uid} ({e})")

# --- Autocomplete helpers ---
async def weekday_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=w, value=w) for w in WEEKDAYS if current in w]

async def period_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=p, value=p) for p in PERIOD_TO_TIME.keys() if current in p]

# ----------------------------
# 新しいヘルパー：試験スケジュール判定を堅牢化
# ----------------------------
def _parse_date_loose(s):
    if not s:
        return None
    try:
        s2 = str(s).strip()
        if "T" in s2:
            s2 = s2.split("T", 1)[0]
        s2 = s2.replace("/", "-")
        return datetime.fromisoformat(s2).date()
    except Exception:
        try:
            return datetime.fromisoformat(str(s)[:10]).date()
        except Exception:
            return None

def day_field_matches(day_field, target_weekday):
    if day_field is None:
        return False
    if isinstance(day_field, int):
        return int(day_field) == int(target_weekday)
    s = str(day_field).strip()
    if s.isdigit():
        try:
            return int(s) == int(target_weekday)
        except Exception:
            pass
    jp_map = {
        "月": 0, "月曜": 0, "月曜日": 0,
        "火": 1, "火曜": 1, "火曜日": 1,
        "水": 2, "水曜": 2, "水曜日": 2,
        "木": 3, "木曜": 3, "木曜日": 3,
        "金": 4, "金曜": 4, "金曜日": 4,
        "土": 5, "土曜": 5, "土曜日": 5,
        "日": 6, "日曜": 6, "日曜日": 6
    }
    if s in jp_map:
        return jp_map[s] == target_weekday
    s_clean = s.replace("曜日", "").replace("曜", "")
    if s_clean in jp_map:
        return jp_map[s_clean] == target_weekday
    eng_map = {
        "mon":0,"monday":0,
        "tue":1,"tues":1,"tuesday":1,
        "wed":2,"wednesday":2,
        "thu":3,"thurs":3,"thursday":3,
        "fri":4,"friday":4,
        "sat":5,"saturday":5,
        "sun":6,"sunday":6
    }
    if s.lower() in eng_map:
        return eng_map[s.lower()] == target_weekday
    return False

def find_active_exam_schedule_for_date(user_data: dict, date_str: str, target_weekday: int):
    schedules = user_data.get("exam_schedules", []) or []
    try:
        target = datetime.fromisoformat(date_str).date()
    except Exception:
        target = _parse_date_loose(date_str)
    if target is None:
        return None, []

    for s in schedules:
        start = _parse_date_loose(s.get("start"))
        end = _parse_date_loose(s.get("end"))
        if start is None or end is None:
            print(f"[WARN] exam schedule '{s.get('name')}' has invalid start/end: {s.get('start')} / {s.get('end')}")
            continue
        if not (start <= target <= end):
            continue

        raw_exam_classes = s.get("classes", []) or []
        filtered = []
        for ec in raw_exam_classes:
            # If class entry has explicit date, match it
            ec_date = ec.get("date")
            if ec_date:
                # Normalize and compare
                try:
                    ec_date_parsed = _parse_date_loose(ec_date)
                    if ec_date_parsed and ec_date_parsed == target:
                        filtered.append(ec.copy())
                except Exception:
                    continue
                continue

            # Otherwise match by day field vs target_weekday
            day_field = ec.get("day")
            if day_field is None:
                continue
            try:
                if day_field_matches(day_field, target_weekday):
                    filtered.append(ec.copy())
            except Exception:
                print(f"[WARN] invalid exam class day field: {ec} (target_weekday={target_weekday})")
                continue

        print(f"[DEBUG-exam-scan] schedule='{s.get('name')}' range={start}/{end} raw_classes={len(raw_exam_classes)} matched={len(filtered)}")
        if filtered:
            return s, filtered

    return None, []

# -----------------------
# 通知タスク（置換用 完全版）
# -----------------------

# グローバルガード（in-memory）
LAST_NOTIFICATION_MINUTE = None

# 朝一覧送信済みマーカー（永続化）
MORNING_MARK_FILE = os.path.join(BASE_DIR, "morning_sent.json")

def load_morning_sent():
    try:
        with open(MORNING_MARK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_morning_sent(d):
    try:
        with open(MORNING_MARK_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        try:
            print(f"[WARN] morning_sent 保存失敗: {e}")
        except:
            pass

# Ensure bot has a lock attribute
def _ensure_notif_lock():
    if not hasattr(bot, "_notif_lock") or bot._notif_lock is None:
        bot._notif_lock = asyncio.Lock()
    return bot._notif_lock

# --- 追加ヘルパー: 補講衝突判定 ---
def _cls_period_time_key(cls, period_overrides):
    """授業エントリから比較に使うキーを返す（優先順: period番号 -> time文字列）。"""
    p = cls.get("period")
    # If period is digit-like, use that
    if p is not None and str(p).isdigit():
        return str(int(str(p)))
    # else try explicit time
    t = period_overrides.get(str(cls.get("period"))) if period_overrides else None
    t = t or cls.get("time") or PERIOD_TO_TIME.get(str(cls.get("period")))
    return (t or "").strip()


def _makeup_conflicts_with_cls(cls, makeups_today, period_overrides):
    """同日補講が存在していて、同じ時限/時刻なら True を返す。"""
    if not makeups_today:
        return False
    cls_key = _cls_period_time_key(cls, period_overrides)
    if not cls_key:
        return False
    for m in makeups_today:
        mk = str(m.get("time") or "").strip()
        # if makeup.time is digit-like, normalize
        if mk.isdigit():
            mk_key = str(int(mk))
        else:
            mk_key = mk
        if mk_key and mk_key == cls_key:
            return True
    return False

async def do_notification_pass(now: datetime = None):
    """
    通知チェック本体。now引数を渡すとその時刻基準で計算します（テスト用/起動キャッチアップに便利）。
    この関数は内でロック & 同分再実行ガードを掛けるため、どの経路から呼ばれても安全です。
    """
    await bot.wait_until_ready()

    # Normalize now
    if now is None:
        now = datetime.now()

    # Ensure lock
    lock = _ensure_notif_lock()

    async with lock:
        # 同分再実行ガード（in-memory）
        global LAST_NOTIFICATION_MINUTE
        minute_key = now.strftime("%Y-%m-%d %H:%M")
        if LAST_NOTIFICATION_MINUTE == minute_key:
            # Already executed in this minute — skip.
            print(f"[INFO] do_notification_pass はこの分({minute_key})に既実行のためスキップします。")
            return
        LAST_NOTIFICATION_MINUTE = minute_key

        # Core local variables
        now_minutes = now.hour * 60 + now.minute
        today_str = now.strftime("%Y-%m-%d")
        today_weekday_actual = now.weekday()

        try:
            # iterate user files
            for file in os.listdir(BASE_DIR):
                if not file.startswith("user_") or not file.endswith(".json"):
                    continue
                try:
                    user_id = int(file.split("_")[1].split(".")[0])
                except Exception:
                    continue

                # fetch user object
                try:
                    user = await bot.fetch_user(user_id)
                except discord.NotFound:
                    continue
                except Exception as e:
                    print(f"[WARN] ユーザー取得失敗: {user_id} ({e})")
                    continue

                data = load_user_data(user_id)
                if not data:
                    continue

                # get cancellations via Gmail (may be empty)
                try:
                    cancellations = fetch_cancellation_emails(user_id) or []
                except Exception as e:
                    print(f"[WARN] Gmail 取得失敗 (user {user_id}): {e}")
                    cancellations = []

                manual_cancellations = data.get("manual_cancellations", []) or []
                classes = data.get("classes", []) or []

                if not classes and not data.get("makeup_classes"):
                    # no classes registered -> nothing to do
                    continue

                # user-defined period time overrides
                period_overrides = data.get("period_overrides", {}) or {}

                # Determine today target weekday (consider per-class overrides for specific dates)
                target_weekday = today_weekday_actual
                for cls in classes:
                    if "overrides" in cls and today_str in cls["overrides"]:
                        target_weekday = cls["overrides"][today_str]
                        break

                # gather today's classes (base scheduled)
                base_today_classes = [c for c in classes if c.get("day") == target_weekday]

                # add makeups (date-specified)
                makeups_today = [m for m in data.get("makeup_classes", []) or [] if m.get("date") == today_str]

                # combine: we want to SUPPRESS canceled original classes if a makeup exists at same date+time
                # First, build a list of final_today_classes starting from base, excluding originals that have both a cancellation and a conflicting makeup.
                final_today_classes = []
                for cls in base_today_classes:
                    # determine whether this class is canceled by manual/email
                    cls_subject = cls.get("subject")
                    canceled_by_manual = any(c.get("date") == today_str and c.get("subject") == cls_subject for c in manual_cancellations)
                    canceled_by_email = any(c.get("date") == today_str and (normalize_text(c.get("subject", "")) == normalize_text(cls_subject) or (c.get("periods") and int(str(cls.get("period")) ) in c.get("periods", []))) for c in cancellations if c.get("date"))

                    conflict_with_makeup = _makeup_conflicts_with_cls(cls, makeups_today, period_overrides)

                    # If canceled AND there's a conflicting makeup, SKIP (i.e., suppress original/休講). Otherwise keep original.
                    if (canceled_by_manual or canceled_by_email) and conflict_with_makeup:
                        print(f"[INFO] user={user_id} 限授業を補講が置き換えるため抑止: {cls.get('subject')} period={cls.get('period')}")
                        continue
                    final_today_classes.append(cls.copy())

                # Now append makeups (they will be notified instead)
                for m in makeups_today:
                    final_today_classes.append({
                        "period": m.get("time", "?"),
                        "time": m.get("time"),
                        "subject": m.get("subject"),
                        "room": m.get("room", "未設定"),
                        "materials": m.get("materials", [])
                    })

                # exam schedule override
                exam_schedule, matched_classes = find_active_exam_schedule_for_date(data, today_str, target_weekday)
                exam_active_name = None
                if exam_schedule and matched_classes:
                    # when exam schedule is active, it replaces today's classes completely
                    final_today_classes = [c.copy() for c in matched_classes]
                    for m in makeups_today:
                        final_today_classes.append({
                            "period": m.get("time", "?"),
                            "time": m.get("time"),
                            "subject": m.get("subject"),
                            "room": m.get("room", "未設定"),
                            "materials": m.get("materials", [])
                        })
                    exam_active_name = exam_schedule.get("name")

                # user notify settings
                user_notify = data.get("notify_settings", {}) or {}
                normal_cfg = user_notify.get("normal") or DEFAULT_NOTIFY["normal"]
                exam_cfg = user_notify.get("exam") or DEFAULT_NOTIFY["exam"]

                # Determine applicable offsets
                offsets_lookup = exam_cfg if exam_active_name else normal_cfg
                try:
                    first_off = int(offsets_lookup.get("first"))
                    second_off = int(offsets_lookup.get("second"))
                except Exception:
                    first_off, second_off = DEFAULT_NOTIFY["normal"]["first"], DEFAULT_NOTIFY["normal"]["second"]
                # Normalize offsets: first always the larger (earlier)
                if first_off < second_off:
                    first_off, second_off = second_off, first_off
                offsets = (first_off, second_off)

                # For traceability
                print(f" user={user_id} 当日授業数={len(final_today_classes)} exam_active={bool(exam_active_name)} offsets={offsets}")

                # Iterate classes for individual reminders
                for cls in final_today_classes:
                    room = cls.get("room", "未設定")
                    if "room_overrides" in cls and today_str in cls["room_overrides"]:
                        room = cls["room_overrides"][today_str]

                    # select time string: user override -> cls.time -> PERIOD_TO_TIME map
                    p_key = str(cls.get("period"))
                    period_overrides = data.get("period_overrides", {})
                    time_str = period_overrides.get(p_key) or cls.get("time") or PERIOD_TO_TIME.get(p_key)
                    if not time_str:
                        # can't parse time; skip
                        print(f"[WARN] 時刻情報なし: user={user_id} subject={cls.get('subject')}")
                        continue
                    try:
                        h, m = map(int, time_str.split(":"))
                        class_minutes = h * 60 + m
                    except Exception as e:
                        print(f"[WARN] 時刻パース失敗: {time_str} ({e}) user={user_id}")
                        continue

                    diff_minutes = class_minutes - now_minutes

                    # 通知設定時間(offsets)に一致するか確認
                    if diff_minutes in offsets:
                        # 持ち物情報の取得
                        mats = parse_materials_field(cls.get("materials"))
                        mats_text = ("\n 持ち物: " + ", ".join(mats)) if mats else ""

                        # 休講判定
                        is_canceled = any(
                            c.get("date") == today_str and 
                            normalize_text(c.get("subject", "")) == normalize_text(cls.get("subject", "")) 
                            for c in (manual_cancellations + cancellations)
                        )
                        
                        # カウントを減らす条件: 最初の通知タイミング(offsets[0]) かつ 休講ではない
                        if diff_minutes == offsets[0] and not is_canceled:
                            subj_norm = normalize_text(cls.get("subject", ""))
                            # 授業データの残り回数を更新
                            for origin_cls in data["classes"]:
                                if normalize_text(origin_cls.get("subject", "")) == subj_norm:
                                    if "remaining" in origin_cls:
                                        origin_cls["remaining"] = max(0, int(origin_cls["remaining"]) - 1)
                            
                            # カウントが0になった授業をリストから削除
                            data["classes"] = [c for c in data["classes"] if c.get("remaining", 1) > 0]
                            save_user_data(user_id, data)

                        # 通知メッセージの作成
                        msg = f"教室「{room}」で{diff_minutes}分後に授業「{cls.get('subject','')}」が始まります{mats_text}"
                        
                        if any(c.get("date") == today_str and c.get("subject") == cls.get("subject") for c in manual_cancellations):
                            msg += "\n※この授業は休講です（手動設定）"
                        elif any(c.get("date") == today_str and c.get("subject") == cls.get("subject") for c in cancellations if c.get("date")):
                            msg += "\n※この授業は休講です（メール取得）"

                        try:
                            await send_dm(user, msg)
                        except Exception as e:
                            print(f"[ERROR] DM送信失敗 (リマインダー) user={user_id}: {e}")
                        
                # Morning summary: send at exact hour:minute if now matches or if now was passed in as 08:00 via catchup (we check exact minute)
                if now.hour == 8 and now.minute == 0:
                    # check persistent morning marker to avoid duplicate
                   # 変更後（ユーザー単位でチェック）
                    morning_marker = load_morning_sent()
                    user_marks = morning_marker.get(str(user_id), {})

                    if user_marks.get(today_str):
                        print(f"[INFO] 本日の朝一覧は既に送信済み (user={user_id}, {today_str})。")
                    else:
                    # 通知送信処理を行う
                    # ...
                    # 保存時もユーザー単位に
                        morning_marker.setdefault(str(user_id), {})[today_str] = True
                        save_morning_sent(morning_marker)

                        if final_today_classes:
                            # Build morning list
                            msg = f"本日の授業一覧（{WEEKDAYS[target_weekday]}）:\n"

                            def sort_key(x):
                                p = x.get("period")
                                try:
                                    return int(p)
                                except Exception:
                                    ts = period_overrides.get(str(p)) or x.get("time") or PERIOD_TO_TIME.get(str(p))
                                    try:
                                        h2, m2 = map(int, ts.split(":"))
                                        return h2*60 + m2
                                    except Exception:
                                        return 99999

                            today_sorted = sorted(final_today_classes, key=sort_key)
                            for cls in today_sorted:
                                room = cls.get("room", "未設定")
                                if "room_overrides" in cls and today_str in cls["room_overrides"]:
                                    room = cls["room_overrides"][today_str]

                                manual_hit = any(c.get("date") == today_str and normalize_text(c.get("subject", "")) == normalize_text(cls.get("subject", "")) for c in manual_cancellations)
                                email_hit = any(g.get("date") == today_str and normalize_text(g.get("subject", "")) == normalize_text(cls.get("subject", "")) for g in cancellations if g.get("date"))

                                note = " ※休講（手動設定）" if manual_hit else (" ※休講（メール取得）" if email_hit else "")
                                period_display = cls.get("period", cls.get("time", "?"))
                                mats = parse_materials_field(cls.get("materials"))
                                mats_text = f" 📎持ち物: {', '.join(mats)}" if mats else ""
                                msg += f"{period_display}限 {cls.get('subject')} ({room}){note}{mats_text}\n"

                            # aggregated today's materials (deduped)
                            agg = aggregate_materials_for_classes(today_sorted)
                            if agg:
                                msg += "\n 本日の持ち物一覧（重複整理済み）:\n"
                                # show simple list
                                msg += "・" + "\n・".join([e["label"] for e in agg]) + "\n"

                            try:
                                await send_long_dm(user, msg)
                                print(f" 本日の授業一覧送信: {user.id}")
                            except Exception as e:
                                print(f"[ERROR] DM送信失敗 (授業一覧) user={user_id}: {e}")

        except Exception as e:
            print(f"[ERROR] do_notification_pass 中の例外: {e}")
            traceback.print_exc()

    # end lock
    return

# ---------------------------
# 常駐ループ: notification_manager
# - このループは 1 分毎に do_notification_pass() を呼びます（分境界に整列）
# - 起動時に「当日の朝一覧が未送信かつ適切な時間帯なら」補完送信を行います（catch-up）
# ---------------------------
async def notification_manager():
    await bot.wait_until_ready()
    _ensure_notif_lock()

    print("[notification_manager] 起動: 通知ループを開始します。")
    # On startup: if now is after 08:00 and morning summary not sent yet, do a catch-up
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    morning_marker = load_morning_sent()
    # catch-up window: 08:00 <= now.hour < 12  (調整可)
    if now.hour >= 8 and now.hour < 12 and not morning_marker.get(today_str):
        print("[notification_manager] 起動時補完: 本日の朝一覧を補完送信します（08:00 ユーザー向け）。")
        # Call do_notification_pass as if it were 08:00 so morning summary logic triggers
        try:
            await do_notification_pass(now=now.replace(hour=8, minute=0, second=0, microsecond=0))
        except Exception as e:
            print(f"[WARN] 起動時補完で例外: {e}")

    # Align to next minute boundary
    while True:
        now = datetime.now()
        # seconds until next minute
        sec = 60 - now.second - now.microsecond/1_000_000
        # avoid tiny negative
        if sec <= 0:
            sec = 0.1
        try:
            await asyncio.sleep(sec)
        except asyncio.CancelledError:
            print("[notification_manager] sleep がキャンセルされました。終了します。")
            return

        # call notification check for the current minute (now ~ exact minute)
        try:
            await do_notification_pass()
        except Exception as e:
            print(f"[ERROR] notification_manager 内で do_notification_pass が例外: {e}")
            traceback.print_exc()

# ----------------------------
# ここから「コマンド群（置き換え用完成版）」を丸ごと貼り付けてください。
# 前提：このファイルの上部ですでに
#   - bot, tree, WEEKDAYS, WEEKDAY_MAP, PERIOD_TO_TIME
#   - load_user_data, save_user_data, send_dm, send_long_dm
#   - weekday_autocomplete, period_autocomplete
#   - その他ユーティリティ群 (parse_materials_field 等)
# が定義されていることを前提とします。
# ----------------------------

# ---------- 追加の Autocomplete ヘルパー ----------
async def subject_autocomplete(interaction: discord.Interaction, current: str):
    """ユーザーの登録授業から科目候補を返す（部分一致）"""
    user_id = interaction.user.id
    data = load_user_data(user_id)
    subjects = []
    for c in data.get("classes", []) or []:
        s = str(c.get("subject", "")).strip()
        if s and current in s:
            subjects.append(s)
    # 補講の科目も候補に入れる
    for m in data.get("makeup_classes", []) or []:
        s = str(m.get("subject", "")).strip()
        if s and current in s:
            subjects.append(s)
    # dedupe while preserving order
    seen = set()
    choices = []
    for s in subjects:
        if s not in seen:
            seen.add(s)
            choices.append(app_commands.Choice(name=s, value=s))
            if len(choices) >= 25:
                break
    # If nothing matched, provide a small helpful hint
    if not choices and current.strip():
        choices.append(app_commands.Choice(name=f"検索候補なし（新規: {current}）", value=current))
    return choices

async def exam_name_autocomplete(interaction: discord.Interaction, current: str):
    user_id = interaction.user.id
    data = load_user_data(user_id)
    schedules = data.get("exam_schedules", []) or []
    choices = []
    for s in schedules:
        name = s.get("name", "")
        if current in name:
            choices.append(app_commands.Choice(name=name, value=name))
            if len(choices) >= 25:
                break
    return choices

async def makeup_time_autocomplete(interaction: discord.Interaction, current: str):
    """補講 time 引数向けの簡易候補（時刻 or 時限番号）"""
    vals = list(PERIOD_TO_TIME.keys()) + list(set(PERIOD_TO_TIME.values()))
    choices = []
    for v in vals:
        if current in str(v):
            choices.append(app_commands.Choice(name=str(v), value=str(v)))
            if len(choices) >= 25:
                break
    return choices

async def room_autocomplete(interaction: discord.Interaction, current: str):
    """ユーザーの授業や補講から教室候補を返す"""
    user_id = interaction.user.id
    data = load_user_data(user_id)
    rooms = []
    for c in data.get("classes", []) or []:
        r = str(c.get("room", "")).strip()
        if r and current in r:
            rooms.append(r)
    for m in data.get("makeup_classes", []) or []:
        r = str(m.get("room", "")).strip()
        if r and current in r:
            rooms.append(r)
    seen = set()
    choices = []
    for r in rooms:
        if r not in seen:
            seen.add(r)
            choices.append(app_commands.Choice(name=r, value=r))
            if len(choices) >= 25:
                break
    return choices
# Gmail / 休講取得関連（認証含む）
mail_group = app_commands.Group(name="mail", description="Gmail 認証および休講取得用コマンド")

@mail_group.command(name="auth", description="Gmail 認証フローを開始します（DMでURL送付）")
async def mail_auth(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = interaction.user.id
    try:
        flow = Flow.from_client_secrets_file(
            GMAIL_CREDENTIALS,
            scopes=['https://www.googleapis.com/auth/gmail.readonly'],
            redirect_uri="https://ninigi05.github.io/oauth-redirect/"
        )
        auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
        # store flow in memory for later /setcode
        try:
            user_auth_flows[user_id] = flow
        except Exception:
            # fallback: ensure variable exists
            globals().setdefault("user_auth_flows", {})[user_id] = flow
        await send_dm(interaction.user,
                      f" Gmail認証を開始します。\n以下のURLを開き、表示された認証コード（code）をコピーしてください：\n\n{auth_url}\n\nコピーしたコードは `/mail setcode <認証コード>` で入力してください。")
        await interaction.followup.send("認証URLをDMに送信しました。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"認証開始に失敗しました: {e}", ephemeral=True)

@mail_group.command(name="setcode", description="Gmail 認証コードを入力して連携を完了します")
@app_commands.describe(code="認証コード")
async def mail_setcode(interaction: discord.Interaction, code: str):
    await interaction.response.defer(ephemeral=True)
    user_id = interaction.user.id
    flows = globals().get("user_auth_flows", {})
    if user_id not in flows:
        await interaction.followup.send(" 先に /mail auth を実行してください。", ephemeral=True)
        return
    try:
        flow = flows[user_id]
        flow.fetch_token(code=code)
        creds = flow.credentials
        token_dir = os.path.join(BASE_DIR, "gmail_tokens")
        os.makedirs(token_dir, exist_ok=True)
        token_file = os.path.join(token_dir, f"user_{user_id}.pickle")
        with open(token_file, "wb") as token:
            pickle.dump(creds, token)
        del flows[user_id]
        await send_dm(interaction.user, " Gmail 認証が完了しました。")
        await interaction.followup.send("認証完了しました。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"認証に失敗しました: {e}", ephemeral=True)

@mail_group.command(name="fetch", description="Gmailから最新の休講情報を取得して登録（DMで要約）")
async def mail_fetch(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = interaction.user.id
    try:
        new_cancellations = fetch_cancellation_emails(user_id)
        user_data = load_user_data(user_id)
        existing = user_data.get("gmail_cancellations", []) or []
        # filter out old ones (keep future or undated)
        today = datetime.now().date()
        filtered = []
        for c in existing:
            d = c.get("date")
            if not d:
                filtered.append(c)
                continue
            try:
                dt = datetime.fromisoformat(d).date()
                if dt >= today:
                    filtered.append(c)
            except Exception:
                filtered.append(c)
        existing = filtered
        # merge dedup
        for nc in new_cancellations:
            dup = False
            for ex in existing:
                if nc.get("date") == ex.get("date") and normalize_text(nc.get("subject","")) == normalize_text(ex.get("subject","")):
                    dup = True
                    break
            if not dup:
                existing.append(nc)
        user_data["gmail_cancellations"] = existing
        save_user_data(user_id, user_data)
        if not existing:
            await send_dm(interaction.user, "📭 登録授業に該当する休講情報は見つかりませんでした。")
            await interaction.followup.send("取得完了（該当なし）。", ephemeral=True)
            return
        lines = [" 最新の休講情報（保存済）:"]
        for c in existing:
            date_display = c.get("date") or "不明日付"
            period_display = c.get("period") or "?"
            subj = c.get("subject") or c.get("subject_header") or "（不明）"
            lines.append(f"{date_display} {period_display}限 {subj}")
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send("休講情報をDMで送信しました。", ephemeral=True)
    except Exception as e:
        print(f"[ERROR] mail_fetch error: {e}")
        traceback.print_exc()
        await interaction.followup.send(f"休講情報の取得に失敗しました: {e}", ephemeral=True)

# ヘルプ / コマンド一覧（カテゴリ別・DM送信）
@tree.command(name="help", description="使い方ヘルプをDMで受け取ります（コマンド一覧をカテゴリ別に表示）")
async def help_command(interaction: discord.Interaction):
    """
    カテゴリ別に見やすいヘルプをDMで送信します。
    ・長文になるため DM（send_long_dm）で送信
    ・DM拒否時はチャット上の一時応答（ephemeral）で通知
    """
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        # defer が使えない環境もあるので黙って進む
        pass

    help_lines = []
    help_lines.append(" **Unipa 授業情報bot — ヘルプ（カテゴリ別）**\n")
    help_lines.append("※このヘルプはDMで届きます。DMを許可していない場合は許可してください。\n")

    # --- クラス管理 ---
    help_lines.append("===  クラス管理（授業の登録 / 表示 / 編集） ===")
    help_lines.append("• /class add weekday period subject room\n  → 授業を登録します。\n  例: /class add 曜日:水曜日 period:2 subject:基礎物理 room:17-404\n  （weekday, period に対しては候補表示（オートコンプリート）があります）")
    help_lines.append("• /class remove weekday period\n  → 登録済み授業を削除します。例: /class remove 火曜日 3\n")
    help_lines.append("• /class list\n  → 自分の登録授業一覧をDMで受け取ります（曜日・時限でソート）")
    help_lines.append("• /class table\n  → 授業一覧を時間割表形式で表示します")
    help_lines.append("• /class setroom date period new_room\n  → 指定日の教室を変更（例: /class setroom 2025-09-22 2 38-S418）")
    help_lines.append("• /class setday date new_weekday\n  → 自分の特定日の曜日を変更（代替時間割対応）\n")

    # --- 補講 / 休講 ---
    help_lines.append("===  補講・休講管理 ===")
    help_lines.append("• /makeup add date time subject room\n  → 補講を追加（例: /makeup add 2025-10-08 15:00 基礎物理 31-506）")
    help_lines.append("• /makeup remove date time\n  → 補講を削除")
    help_lines.append("• /makeup list\n  → 補講一覧を表示")
    help_lines.append("• /cancel add date subject\n  → 手動で休講を追加（例: /cancel add 2025-10-08 総合的な学習の時間）")
    help_lines.append("• /cancel remove date subject\n  → 手動休講を削除")
    help_lines.append("• /cancel list\n  → 手動で追加した休講一覧を表示")
    help_lines.append("• /mail fetch\n  → Gmail から取得した最新の休講情報を確認して保存します（Gmail 認証要）\n")

    # --- 試験時間割 ---
    help_lines.append("===  試験時間割（exam グループ） ===")
    help_lines.append("• /exam create name start end\n  → 試験用時間割を作成（例: /exam create 前期試験 2025-07-20 2025-07-25）")
    help_lines.append("• /exam list\n  → 登録済みの試験時間割を一覧表示")
    help_lines.append("• /exam show name\n  → 指定時間割の中身を表示")
    help_lines.append("• /exam addclass name weekday period subject room [time]\n  → 試験時間割へ授業を追加（weekday/period に候補表示あり）")
    help_lines.append("• /exam removeclass name weekday period\n  → 試験時間割から授業を削除\n")

    # --- Gmail / 認証 ---
    help_lines.append("===  Gmail 認証 / 連携 ===")
    help_lines.append("• /mail auth\n  → Gmail 認証を開始します。認証URLはDMで送信されます。")
    help_lines.append("• /mail setcode code\n  → /mail auth で取得した認証コードを入力して完了します（必須）\n")

    # --- 通知設定 / 今日の授業 ---
    help_lines.append("===  通知・今日の授業 ===")
    help_lines.append("• /notify set type first second\n  → 通知時刻を設定（type は normal または exam）。例: /notify set normal 30 15")
    help_lines.append("• /notify set_period period time\n  → 各時限の開始時間を設定（例: /notify set_period 1 09:00）")
    help_lines.append("• /notify show\n  → 現在の通知設定を確認（DM）\n")

    # --- その他（ユーティリティ） ---
    help_lines.append("===  その他ユーティリティ ===")
    help_lines.append("• /mail fetch\n  → Gmail からの休講を取り込み、ユーザーデータに保存します")
    help_lines.append("• /setting period_time period time\n  → 各時限の開始時刻をグローバルに設定します")
    help_lines.append("• /exam create/list/show/addclass/removeclass など、引数に対して自動候補（オートコンプリート）が使えます")
    help_lines.append("• コマンドはすべてスラッシュコマンドです。引数候補は引数入力中に表示されます。\n")

    # --- 注意事項 ---
    help_lines.append("===  注意事項 ===")
    help_lines.append("• 長い出力は複数メッセージに分割してDMで送信します。")
    help_lines.append("• Gmail 関連機能を使うには /authmail → /setcode の順で認証を行ってください。")
    help_lines.append("• /help は DM 送信を基本とします（DM不可の場合はここでエラーを通知します）。")
    help_lines.append("\n必要ならカテゴリ別の詳細ヘルプ（例: /help classes /help exam）も作れます。ご希望あれば追加します。")

    help_text = "\n".join(help_lines)

    # send via DM (長文は send_long_dm を使う)
    try:
        await send_long_dm(interaction.user, help_text)
        try:
            await interaction.followup.send(" ヘルプをDMで送信しました。DMを確認してください。", ephemeral=True)
        except Exception:
            # 最低限の反応
            pass
    except discord.Forbidden:
        # DM拒否時はチャット上で短く通知（ephemeral）
        try:
            await interaction.followup.send(" DM にヘルプを送信できませんでした。DMを許可しているか確認してください。", ephemeral=True)
        except Exception:
            pass
    except Exception as e:
        # 万一のエラーはログ出力してユーザーへ簡単に通知
        print(f"[ERROR] help_command DM 送信失敗: {e}")
        try:
            await interaction.followup.send(" ヘルプ送信中にエラーが発生しました。管理者に問い合わせてください。", ephemeral=True)
        except Exception:
            pass

@bot.event
async def on_ready():
    print(f"Bot 起動完了: {bot.user}")
    guild = None
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
    try:
        if guild:
            synced_guild = await tree.sync(guild=guild)
            print(f" ギルドにコマンド同期: {[cmd.name for cmd in synced_guild]}")
        else:
            print(" GUILD_ID が設定されていません。ギルド同期をスキップします。")
    except Exception as e:
        print(f"ギルド同期失敗: {e}")
    try:
        synced_global = await tree.sync()
        print(f" グローバルにコマンド同期: {len(synced_global)} 件")
    except Exception as e:
        print(f"グローバル同期失敗: {e}")

    # notification_manager をバックグラウンドで起動（一本化）
    if getattr(bot, "_notification_manager_task", None) is None:
        bot._notification_manager_task = asyncio.create_task(notification_manager())
        print(" notification_manager タスクを起動しました（バックグラウンド）")
    else:
        print(" notification_manager は既に起動済みです。")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
