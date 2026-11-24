"""
Gmail から休講・授業変更のメールを取得して解析する処理（/mail コマンドと自動スキャンで共用）
"""

import os
import base64
import logging
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import urllib.parse
import urllib.request
from email.utils import parseaddr

# 公式の休講・授業変更メールとして確認できた送信者（2026-11-24 実績調査に基づく完全一致リスト）
OFFICIAL_MAIL_SENDERS = frozenset(
    {
        "unipa_send_only@itp.kindai.ac.jp",  # 近畿大学メール配信専用（UNIPA の公式通知）
        "no-reply@classroom.google.com",  # Google Classroom のお知らせ（先生名は表示名に入る）
        "no-reply@kdu.mobi",  # KDU Mobile
    }
)


def parse_sender(from_header: str) -> tuple[str, str]:
    """From ヘッダーから (アドレス小文字, 表示名) を取り出す。判定は表示名でなくアドレスで行う。"""
    display, addr = parseaddr(from_header or "")
    return (addr or "").strip().lower(), (display or "").strip()


def is_official_sender(from_header: str) -> bool:
    """送信者アドレスが公式リストに完全一致するか。"""
    addr, _ = parse_sender(from_header)
    return bool(addr) and addr in OFFICIAL_MAIL_SENDERS


def sender_label(from_header: str) -> str:
    """DM 表示用の送信者表記（表示名があれば「表示名 <アドレス>」、なければアドレスのみ）。"""
    addr, display = parse_sender(from_header)
    if display and addr:
        return f"{display} <{addr}>"
    return addr or display or "（送信者不明）"

from googleapiclient.discovery import build  # type: ignore[import-untyped]
from google.auth.transport.requests import Request

from utils import (
    load_user_data,
    get_term_for_date,
    get_regular_classes_on_date,
)
import token_store

logger = logging.getLogger(__name__)
BASE_DIR = os.getcwd()
TOKEN_DIR = os.path.join(BASE_DIR, "gmail_tokens")
APP_TIMEZONE = (os.getenv("APP_TIMEZONE") or "Asia/Tokyo").strip()
MAIL_QUERY = "subject:(休講 OR 補講 OR 授業変更 OR 曜日変更 OR 振替) newer_than:14d"


def now_local() -> datetime:
    try:
        return datetime.now(ZoneInfo(APP_TIMEZONE))
    except Exception:
        return datetime.now()


def token_path(user_id) -> str:
    return os.path.join(TOKEN_DIR, f"user_{user_id}.pickle")


def has_gmail_token(user_id) -> bool:
    return token_store.credentials_exist(user_id)


def list_linked_user_ids() -> list[int]:
    """Gmail トークンを保存している利用者の ID 一覧。"""
    if not os.path.isdir(TOKEN_DIR):
        return []
    ids = []
    for name in os.listdir(TOKEN_DIR):
        m = re.fullmatch(r"user_(\d+)\.(?:pickle|json)", name)
        if m:
            ids.append(int(m.group(1)))
    return ids


def unlink_gmail(user_id) -> bool:
    """Gmail 連携を解除する（Google 側のトークンを取り消し、保存したトークンを削除する）。

    トークンを削除したら True。Google 側の取り消しに失敗しても、保存したトークンは削除する。
    """
    removed = False
    if token_store.credentials_exist(user_id):
        try:
            creds = token_store.load_credentials(user_id)
            token = getattr(creds, "refresh_token", None) or getattr(creds, "token", None)
            if token:
                req = urllib.request.Request(
                    "https://oauth2.googleapis.com/revoke",
                    data=urllib.parse.urlencode({"token": token}).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:
            logger.warning(f"[Gmail] トークンの取り消しに失敗 user={user_id}: {e}")
        removed = token_store.delete_credentials(user_id)
    return removed


# --- Gmailサービス取得 ---
def get_gmail_service(user_id):
    creds = token_store.load_credentials(user_id)

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
                    token_store.save_credentials(user_id, creds)
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


def _infer_year(month: int, day: int) -> int:
    """年の記載がない日付の年を推定する（12月に届いた1月の通知などで年がずれないようにする）。"""
    today = now_local().date()
    for year in (today.year, today.year + 1, today.year - 1):
        try:
            candidate = datetime(year, month, day).date()
        except ValueError:
            continue
        if abs((candidate - today).days) <= 183:
            return year
    return today.year


# 曜日変更（授業変更日）と判定する件名と、実施を表す本文の表現
# 例: 「月曜日の授業を行います」「水曜日の時間割で実施」「火曜授業を実施」
SCHEDULE_CHANGE_SUBJECT_RE = re.compile(r"授業変更|曜日変更|振替")
SCHEDULE_CHANGE_BODY_RE = re.compile(
    r"([月火水木金土日])曜(?:日)?(?:の)?(?:授業|時間割)(?:を|で|に|と)?(?:して)?(?:行|実施)"
)


def parse_cancellation_email(mail):
    subject_header = (mail.get("subject") or "").strip()
    body = (mail.get("body") or "").strip()

    date_match = re.search(
        r"(?:休講日|実施日|月日)\s*[：:]\s*(?:(令和\s*(\d+)年)|(\d{4})年)?\s*(\d{1,2})月\s*(\d{1,2})日",
        body,
    )
    if not date_match:
        date_match2 = re.search(r"(\d{1,2})月\s*(\d{1,2})日", subject_header)
        if date_match2:
            month = int(date_match2.group(1))
            day = int(date_match2.group(2))
            year = _infer_year(month, day)
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
            year = _infer_year(month, day)
        date_str = f"{year}-{month:02d}-{day:02d}"

    # 曜日変更（授業変更日）の判定
    # 「金曜日の授業を休講」のような休講通知を誤判定しないよう、件名が授業変更系で休講を含まず、
    # 本文が「〇曜日の授業を行う／実施」のように実施を表す場合に限る
    change_match = None
    if SCHEDULE_CHANGE_SUBJECT_RE.search(subject_header) and "休講" not in subject_header:
        change_match = SCHEDULE_CHANGE_BODY_RE.search(body + subject_header)
    if change_match:
        target_weekday = change_match.group(1) + "曜日"
        return {
            "type": "schedule_change",
            "date": date_str,
            "target_weekday": target_weekday,
            "body": body,
            "subject_header": subject_header,
        }

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
        "type": "cancellation",
        "date": date_str,
        "period": period_display,
        "periods": periods,
        "subject": course_name,
        "body": body,
        "subject_header": subject_header,
    }


def scan_mail(user_id, skip_ids=()):
    """直近14日の休講・授業変更メールを調べる。

    skip_ids のメールは本文を取得しない（自動スキャンで調べ済みのメールを除くため）。
    戻り値は (該当した通知のリスト, 検索に一致した全メールの ID)。Gmail を使えないときは None。
    休講の通知には、時間割で一致した授業の時限を matched_periods に入れる。
    """
    service = get_gmail_service(user_id)
    if not service:
        logger.info(f"Gmail認証が必要です（fetchスキップ）: user {user_id}")
        return None

    results = (
        service.users()
        .messages()
        .list(userId="me", q=MAIL_QUERY, maxResults=50)
        .execute()
    )
    messages = results.get("messages", [])
    message_ids = [msg["id"] for msg in messages]
    skip_ids = set(skip_ids)
    raw_list = []

    for msg in messages:
        if msg["id"] in skip_ids:
            continue
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
        from_hdr = next(
            (h["value"] for h in headers if h.get("name") == "From"), ""
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

        raw_list.append(
            {"id": msg["id"], "subject": subject_hdr, "body": body_text, "from": from_hdr}
        )

    user_data = load_user_data(user_id)

    matches = []
    for raw in raw_list:
        parsed = parse_cancellation_email(raw)
        if not parsed:
            continue
        parsed["message_id"] = raw["id"]
        parsed["sender"] = sender_label(raw.get("from") or "")
        parsed["is_official_sender"] = is_official_sender(raw.get("from") or "")

        # 授業変更日の場合は、ユーザーの授業とのマッチングを行わずにリストに追加
        if parsed.get("type") == "schedule_change":
            matches.append(parsed)
            continue

        # 以下、休講のマッチングロジック
        # メールの日付が属する学期の、その日に実施される授業（曜日変更を反映）と照合する
        try:
            date_str = datetime.fromisoformat(parsed["date"]).date().isoformat()
            classes = get_regular_classes_on_date(
                user_data, get_term_for_date(user_data, date_str), date_str
            )
        except Exception:
            continue

        parsed_subject = parsed.get("subject") or parsed.get("course") or ""
        parsed_periods = parsed.get("periods") or []
        if not parsed_periods:
            p = parsed.get("period") or parsed.get("period_raw") or ""
            if p:
                parsed_periods = extract_numbers(str(p))

        matched_periods = []
        for cls in classes:
            cls_subject = cls.get("subject", "") or ""
            cls_period = cls.get("period")

            if parsed_subject:
                ok = subjects_match_strict(
                    parsed_subject, cls_subject, parsed_periods, cls_period
                )
            else:
                try:
                    cls_p_int = int(str(cls_period))
                except Exception:
                    cls_p_int = None
                ok = bool(cls_p_int and parsed_periods and cls_p_int in parsed_periods)
            if ok:
                matched_periods.append(str(cls_period))

        if matched_periods:
            parsed["matched_periods"] = sorted(set(matched_periods))
            matches.append(parsed)
        else:
            logger.info(
                f"[DEBUG-no-match] user={user_id} 未一致: parsed_subject='{parsed_subject}' date={parsed['date']}"
            )

    return matches, message_ids


def fetch_cancellation_emails(user_id):
    """/mail fetch 用。該当した通知のリストを返す（失敗時は空）。"""
    try:
        result = scan_mail(user_id)
    except Exception as e:
        logger.exception(f"Gmail休講情報の取得に失敗しました: {e}")
        return []
    return result[0] if result else []
