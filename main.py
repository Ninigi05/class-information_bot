import os
import asyncio
import traceback
import logging
import glob
import ssl
import socket
import copy
import hashlib
import json
import time
import uuid
from typing import Optional

from logging.handlers import RotatingFileHandler
import discord
import sys
from discord import app_commands
from discord.ext import commands
import aiohttp
import certifi
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo
import gmail_oauth

from utils import (
    load_user_data,
    save_user_data,
    send_dm,
    send_long_dm,
    WEEKDAYS,
    WEEKDAY_MAP,
    get_current_term,
    replace_day_overrides,
    replace_room_overrides,
    sync_exams_after_classes_replaced,
    normalize_term_key,
    TERM_FIRST,
    TERM_SECOND,
)
from web_api_integration import start_web_server
from web_link_service import consume_link_key


def _supports_app_command_context_features() -> bool:
    """discord.py の app command context / install API が使えるか判定する。"""
    required_attrs = (
        "AppInstallationType",
        "AppCommandContext",
        "allowed_installs",
        "allowed_contexts",
    )
    return all(hasattr(app_commands, attr) for attr in required_attrs)


load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID") or 0)
SYNC_GUILD_COMMANDS = (os.getenv("SYNC_GUILD_COMMANDS") or "0").strip() in {
    "1",
    "true",
    "yes",
    "on",
}
APP_TIMEZONE = (os.getenv("APP_TIMEZONE") or "Asia/Tokyo").strip()


class AppTimezoneFormatter(logging.Formatter):
    """Format log timestamps in APP_TIMEZONE regardless of host/container localtime."""

    def formatTime(self, record, datefmt=None):
        try:
            dt = datetime.fromtimestamp(record.created, ZoneInfo(APP_TIMEZONE))
        except Exception:
            dt = datetime.fromtimestamp(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="seconds")


def now_local() -> datetime:
    """Return current time in configured application timezone."""
    try:
        return datetime.now(ZoneInfo(APP_TIMEZONE))
    except Exception:
        # Fallback to system local time if timezone config is invalid.
        return datetime.now()


def build_discord_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if hasattr(ssl.TLSVersion, "TLSv1_3"):
        context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def build_discord_connector() -> aiohttp.TCPConnector:
    force_ipv4 = (os.getenv("DISCORD_FORCE_IPV4") or "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    dns_ttl = int((os.getenv("AIOHTTP_DNS_TTL") or "60").strip())

    return aiohttp.TCPConnector(
        ssl=build_discord_ssl_context(),
        family=socket.AF_INET if force_ipv4 else socket.AF_UNSPEC,
        ttl_dns_cache=dns_ttl,
        enable_cleanup_closed=True,
    )


GMAIL_CREDENTIALS = gmail_oauth.resolve_credentials_path()

BASE_DIR = os.getcwd()


def configure_logging():
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "bot.log")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if root_logger.handlers:
        root_logger.handlers.clear()

    formatter = AppTimezoneFormatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %z",
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
logger.info(
    "[TIME] APP_TIMEZONE=%s now_local=%s now_utc=%s",
    APP_TIMEZONE,
    now_local().strftime("%Y-%m-%d %H:%M:%S %z"),
    datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
)


def _apply_web_payload_to_user_data(user_data: dict, payload: dict) -> dict:
    """Web の payload に含まれる項目のみ user_data へ反映する（term-aware）。"""
    meta = payload.get("_meta") or {}
    feature = (meta.get("feature") or "all").strip()
    term = normalize_term_key(meta.get("term"))
    target_terms = [TERM_FIRST, TERM_SECOND] if feature == "all" else [term]

    user_data.setdefault("classes_by_term", {}).setdefault(term, [])
    user_data.setdefault("exam_schedules_by_term", {}).setdefault(term, [])

    for target_term in target_terms:
        user_data.setdefault("classes_by_term", {}).setdefault(target_term, [])
        user_data.setdefault("exam_schedules_by_term", {}).setdefault(target_term, [])

    def _convert_classes(classes_raw: list) -> list[dict]:
        classes: list[dict] = []
        for c in classes_raw:
            wd = str(c.get("weekday", "")).strip()
            if wd not in WEEKDAY_MAP:
                continue
            classes.append(
                {
                    "day": WEEKDAY_MAP[wd],
                    "period": str(c.get("period", "")).strip(),
                    "subject": str(c.get("subject", "")).strip(),
                    "room": str(c.get("room", "")).strip(),
                    "has_exam": c.get("has_exam", True) is not False,
                }
            )
        return classes

    def _convert_exam_schedules(schedules_raw: list) -> list[dict]:
        schedules_out = []
        for s in schedules_raw:
            classes_out = []
            for ec in s.get("classes") or []:
                wd = str(ec.get("weekday", "")).strip()
                if wd not in WEEKDAY_MAP:
                    continue
                classes_out.append(
                    {
                        "day": WEEKDAY_MAP[wd],
                        "period": str(ec.get("period", "")).strip(),
                        "time": ec.get("time"),
                        "subject": str(ec.get("subject", "")).strip(),
                        "room": str(ec.get("room", "")).strip(),
                    }
                )
            schedules_out.append(
                {
                    "name": str(s.get("name", "")).strip(),
                    "start": str(s.get("start", "")).strip(),
                    "end": str(s.get("end", "")).strip(),
                    "classes": classes_out,
                }
            )
        return schedules_out

    old_classes = {
        t: list(user_data.get("classes_by_term", {}).get(t, []) or [])
        for t in (TERM_FIRST, TERM_SECOND)
    }
    if "classes_by_term" in payload and isinstance(
        payload.get("classes_by_term"), dict
    ):
        for raw_term, classes_raw in (payload.get("classes_by_term") or {}).items():
            target_term = normalize_term_key(raw_term)
            if target_term not in {TERM_FIRST, TERM_SECOND}:
                continue
            user_data.setdefault("classes_by_term", {}).setdefault(target_term, [])
            user_data["classes_by_term"][target_term] = _convert_classes(
                classes_raw or []
            )
    elif "classes" in payload:
        # 単一リスト形式の授業は、キーを発行した学期（_meta.term）分だけ。
        # feature=all でも両学期へは書き込まない（前期が後期分で消えるバグの修正）。
        classes_raw = payload.get("classes") or []
        classes = _convert_classes(classes_raw)
        user_data["classes_by_term"][term] = list(classes)

    # 曜日変更・教室変更（Web の内容で置き換える）
    if "day_overrides" in payload:
        replace_day_overrides(user_data, payload.get("day_overrides") or [])
    if "room_overrides" in payload:
        replace_room_overrides(user_data, payload.get("room_overrides") or [])

    # 通知設定
    if "notify" in payload:
        notify = payload.get("notify") or {}
        user_data.setdefault("notify_settings", {})
        normal_cfg = user_data["notify_settings"].get("normal") or {}
        exam_cfg = user_data["notify_settings"].get("exam") or {}
        user_data["notify_settings"]["normal"] = {
            "first": int(notify.get("normal_first", normal_cfg.get("first", 15))),
            "second": int(notify.get("normal_second", normal_cfg.get("second", 10))),
        }
        user_data["notify_settings"]["exam"] = {
            "first": int(notify.get("exam_first", exam_cfg.get("first", 30))),
            "second": int(notify.get("exam_second", exam_cfg.get("second", 25))),
        }
        if "morning_time" in notify:
            user_data["morning_notice_time"] = str(notify.get("morning_time", "08:00"))

    # 時限上書き
    if "period_overrides" in payload:
        user_data["period_overrides"] = payload.get("period_overrides") or {}
    if "exam_period_overrides" in payload:
        user_data["exam_period_overrides"] = payload.get("exam_period_overrides") or {}

    # 試験時間割（Web入力をそのまま優先）
    if "exam_schedules_by_term" in payload and isinstance(
        payload.get("exam_schedules_by_term"), dict
    ):
        for raw_term, schedules_raw in (
            payload.get("exam_schedules_by_term") or {}
        ).items():
            target_term = normalize_term_key(raw_term)
            if target_term not in {TERM_FIRST, TERM_SECOND}:
                continue
            user_data.setdefault("exam_schedules_by_term", {}).setdefault(
                target_term, []
            )
            user_data["exam_schedules_by_term"][target_term] = _convert_exam_schedules(
                schedules_raw or []
            )
    elif "exam_schedules" in payload:
        # 単一リスト形式の試験時間割は、キーを発行した学期（_meta.term）分だけ。
        # feature=all でも両学期へは書き込まない（前期が後期分で消えるバグの修正）。
        schedules_out = _convert_exam_schedules(payload.get("exam_schedules") or [])
        user_data["exam_schedules_by_term"][term] = list(schedules_out)

    # 試験時間割を取り込まなかった場合は、定期試験ありの授業を既存の試験時間割へ反映する
    if "exam_schedules_by_term" not in payload and "exam_schedules" not in payload:
        for t, old in old_classes.items():
            if user_data.get("classes_by_term", {}).get(t, []) != old:
                sync_exams_after_classes_replaced(user_data, t, old)

    # 学期開始日と授業回数設定
    if "term_start_dates" in payload and isinstance(
        payload.get("term_start_dates"), dict
    ):
        user_data.setdefault("term_start_dates", {}).update(
            payload.get("term_start_dates") or {}
        )
    if "term_ranges" in payload and isinstance(payload.get("term_ranges"), dict):
        user_data.setdefault("term_ranges", {}).update(payload.get("term_ranges") or {})

    if "class_count_targets" in payload and isinstance(
        payload.get("class_count_targets"), dict
    ):
        # 目標が実際に変わった学期だけ、出席カウントをリセットする。
        # 空・同値のまま取り込んでも消えないようにする（通知キーにも
        # class_count_targets が常に含まれるため、無条件リセットは事故になる）。
        old_targets = user_data.get("class_count_targets") or {}
        changed_terms: list[str] = []
        new_targets = dict(old_targets)
        for raw_term, raw_value in (payload.get("class_count_targets") or {}).items():
            target_term = normalize_term_key(raw_term)
            if target_term not in {TERM_FIRST, TERM_SECOND}:
                continue
            if old_targets.get(target_term) != raw_value:
                changed_terms.append(target_term)
            new_targets[target_term] = raw_value
        user_data["class_count_targets"] = new_targets
        if changed_terms:
            attendance = user_data.get("class_attendance_count", {}) or {}
            for changed_term in changed_terms:
                attendance[changed_term] = {}
            user_data["class_attendance_count"] = attendance

    return user_data


async def _apply_gmail_auth_code_from_web(
    user_id: int, code: str, state: str | None
) -> bool:
    """Webで取得した Gmail OAuth code を、発行時の state（PKCE）と照合してトークン保存する。"""
    if not code:
        return False
    if not state:
        # state がない古い形式のキーは、発行者を確認できないため受け付けない
        logger.warning(f"[WARN] Web経由の Gmail 認証に state がありません user={user_id}")
        return False
    try:
        await asyncio.to_thread(
            gmail_oauth.exchange_code, GMAIL_CREDENTIALS, user_id, code, state=state
        )
        return True
    except Exception as e:
        logger.warning(f"[WARN] Web経由の Gmail 認証反映失敗 user={user_id}: {e}")
        return False


# --- 連携キー適用前確認（差分・保留・バックアップ） ---

_PENDING_LINK_APPLY_TTL_SECONDS = 600
_pending_link_applies: dict[int, dict] = {}


def _user_data_digest(data) -> str:
    raw = json.dumps(dict(data or {}), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _class_slot_map(classes) -> dict:
    out = {}
    for c in classes or []:
        out[(c.get("day"), str(c.get("period")))] = c
    return out


def _format_class_for_diff(c) -> str:
    day = c.get("day")
    day_name = WEEKDAYS[day] if isinstance(day, int) and 0 <= day < len(WEEKDAYS) else "不明"
    return f"{day_name} {c.get('period')}限: {c.get('subject')} ({c.get('room')})"


def _build_apply_diff_lines(old, new, payload) -> list[str]:
    old = old or {}
    new = new or {}
    payload = payload or {}
    lines: list[str] = []

    for term in (TERM_FIRST, TERM_SECOND):
        old_map = _class_slot_map((old.get("classes_by_term") or {}).get(term))
        new_map = _class_slot_map((new.get("classes_by_term") or {}).get(term))
        if old_map != new_map:
            lines.append(f"【{term}】授業: {len(old_map)}件 → {len(new_map)}件")
            for slot in sorted(set(old_map) | set(new_map), key=lambda s: (str(s[0]), str(s[1]))):
                o, n = old_map.get(slot), new_map.get(slot)
                if o is None:
                    lines.append(f"  ・追加: {_format_class_for_diff(n)}")
                elif n is None:
                    lines.append(f"  ・削除: {_format_class_for_diff(o)}")
                elif o != n:
                    lines.append(f"  ・変更: {_format_class_for_diff(o)} → {_format_class_for_diff(n)}")

    for term in (TERM_FIRST, TERM_SECOND):
        old_exams = (old.get("exam_schedules_by_term") or {}).get(term) or []
        new_exams = (new.get("exam_schedules_by_term") or {}).get(term) or []
        if old_exams != new_exams:
            old_names = [s.get("name") for s in old_exams]
            new_names = [s.get("name") for s in new_exams]
            lines.append(f"【{term}】試験時間割: {len(old_exams)}件 → {len(new_exams)}件")
            for name in new_names:
                if name not in old_names:
                    lines.append(f"  ・追加: {name}")
            for name in old_names:
                if name not in new_names:
                    lines.append(f"  ・削除: {name}")

    if old.get("notify_settings") != new.get("notify_settings"):
        lines.append("【通知設定】変更があります")
    if old.get("morning_notice_time") != new.get("morning_notice_time"):
        lines.append(f"【朝の通知時刻】{old.get('morning_notice_time')} → {new.get('morning_notice_time')}")

    for key, label in (("period_overrides", "時限上書き"), ("exam_period_overrides", "試験時限上書き")):
        if old.get(key) != new.get(key):
            lines.append(f"【{label}】変更があります")
    for key, label in (("day_overrides", "曜日上書き"), ("room_overrides", "教室上書き")):
        if old.get(key) != new.get(key):
            lines.append(f"【{label}】{len(old.get(key) or [])}件 → {len(new.get(key) or [])}件")

    if old.get("term_start_dates") != new.get("term_start_dates") or old.get("term_ranges") != new.get("term_ranges"):
        lines.append("【学期の日程設定】変更があります")

    if old.get("class_count_targets") != new.get("class_count_targets"):
        lines.append(f"【授業回数の目標】{old.get('class_count_targets') or {}} → {new.get('class_count_targets') or {}}")
    for term in (TERM_FIRST, TERM_SECOND):
        o_att = (old.get("class_attendance_count") or {}).get(term) or {}
        n_att = (new.get("class_attendance_count") or {}).get(term) or {}
        if o_att != n_att:
            if o_att and not n_att:
                lines.append(f"⚠ 【{term}】出席カウントはリセットされます（目標変更のため）")
            else:
                lines.append(f"【{term}】出席カウントが変更されます")

    if (payload.get("gmail_auth_code") or "").strip():
        lines.append("⚠ このキーには Gmail 連携が含まれています。適用すると Gmail 連携が更新されます。")

    if len(lines) > 30:
        lines = lines[:30] + [f"  …他 {len(lines) - 30} 件の変更"]
    return lines


def _stash_pending_link_apply(user_id: int, payload: dict, base_digest: str) -> str:
    token = uuid.uuid4().hex
    _pending_link_applies[user_id] = {
        "token": token,
        "payload": payload,
        "base_digest": base_digest,
        "created_monotonic": time.monotonic(),
    }
    return token


def _get_pending_link_apply(user_id: int) -> dict | None:
    pending = _pending_link_applies.get(user_id)
    if not pending:
        return None
    if time.monotonic() - float(pending.get("created_monotonic") or 0) > _PENDING_LINK_APPLY_TTL_SECONDS:
        _pending_link_applies.pop(user_id, None)
        return None
    return pending


def _discard_pending_link_apply(user_id: int) -> None:
    _pending_link_applies.pop(user_id, None)


def _pending_apply_is_fresh(pending: dict | None, current_data) -> bool:
    return bool(pending) and pending.get("base_digest") == _user_data_digest(current_data)


def _apply_backup_dir(base_dir: str | None = None) -> str:
    return os.path.join(base_dir or os.getcwd(), "backups")


def _save_apply_backup(user_id: int, data, base_dir: str | None = None) -> str:
    dirpath = _apply_backup_dir(base_dir)
    os.makedirs(dirpath, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"user_{user_id}_{stamp}.json"
    counter = 1
    while os.path.exists(os.path.join(dirpath, filename)):
        counter += 1
        filename = f"user_{user_id}_{stamp}-{counter}.json"
    with open(os.path.join(dirpath, filename), "w", encoding="utf-8") as f:
        json.dump(dict(data or {}), f, ensure_ascii=False, indent=2)
    files = sorted(f for f in os.listdir(dirpath) if f.startswith(f"user_{user_id}_") and f.endswith(".json"))
    for old_file in files[:-3]:
        try:
            os.remove(os.path.join(dirpath, old_file))
        except OSError:
            pass
    return filename


def _restore_apply_backup(user_id: int, filename: str, base_dir: str | None = None) -> dict | None:
    if not filename or os.path.basename(filename) != filename:
        return None
    if not filename.startswith(f"user_{user_id}_"):
        return None
    path = os.path.join(_apply_backup_dir(base_dir), filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


class LinkApplyRollbackView(discord.ui.View):
    def __init__(self, user_id: int, backup_filename: str, timeout: float = 3600):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.backup_filename = backup_filename

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="適用前に戻す", style=discord.ButtonStyle.danger)
    async def rollback(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = _restore_apply_backup(self.user_id, self.backup_filename)
        if data is None:
            await interaction.response.send_message("バックアップが見つかりませんでした。", ephemeral=True)
            return
        save_user_data(self.user_id, data)
        button.disabled = True
        self.stop()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("連携キーを適用する前の状態に戻しました。", ephemeral=True)


class LinkApplyConfirmView(discord.ui.View):
    def __init__(self, user_id: int, token: str, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.token = token

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_timeout(self):
        pending = _get_pending_link_apply(self.user_id)
        if pending and pending.get("token") == self.token:
            _discard_pending_link_apply(self.user_id)
        for item in self.children:
            item.disabled = True

    def _disable_all(self):
        for item in self.children:
            item.disabled = True
        self.stop()

    @discord.ui.button(label="この内容で適用する", style=discord.ButtonStyle.primary)
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = _get_pending_link_apply(self.user_id)
        if not pending or pending.get("token") != self.token:
            self._disable_all()
            await interaction.response.edit_message(
                content="この確認は期限切れか、既に処理されています。必要なら /web applykey を実行し直してください。",
                view=self,
            )
            return

        payload = pending.get("payload") or {}
        user_data = load_user_data(self.user_id)
        if not _pending_apply_is_fresh(pending, user_data):
            new_data = _apply_web_payload_to_user_data(copy.deepcopy(dict(user_data)), payload)
            diff_lines = _build_apply_diff_lines(dict(user_data), new_data, payload)
            pending["base_digest"] = _user_data_digest(user_data)
            feature = ((payload.get("_meta") or {}).get("feature") or "all").strip()
            term = normalize_term_key((payload.get("_meta") or {}).get("term"))
            await interaction.response.edit_message(
                content=(
                    "確認中にデータが変更されていたため、差分を作り直しました。"
                    "内容を確認して、もう一度ボタンを押してください。\n\n"
                    + _format_link_apply_preview(feature, term, diff_lines)
                ),
                view=self,
            )
            return

        _discard_pending_link_apply(self.user_id)
        backup_filename = _save_apply_backup(self.user_id, dict(user_data))
        user_data = _apply_web_payload_to_user_data(user_data, payload)
        save_user_data(self.user_id, user_data)

        gmail_ok = False
        gmail_code = (payload.get("gmail_auth_code") or "").strip()
        if gmail_code:
            gmail_ok = await _apply_gmail_auth_code_from_web(
                self.user_id, gmail_code, (payload.get("gmail_auth_state") or "").strip()
            )

        self._disable_all()
        await interaction.response.edit_message(content="連携キーを適用しました。詳細をDMに送信しました。", view=self)
        await _send_apply_result_dm(interaction.user, user_data, payload, gmail_ok)
        await interaction.followup.send(
            "適用前のデータはバックアップされています。戻す場合はこのボタンを押してください（1時間有効）。",
            view=LinkApplyRollbackView(self.user_id, backup_filename),
            ephemeral=True,
        )

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = _get_pending_link_apply(self.user_id)
        if pending and pending.get("token") == self.token:
            _discard_pending_link_apply(self.user_id)
        self._disable_all()
        await interaction.response.edit_message(
            content="キャンセルしました。データは変更されていません（キーは消費済みのため、使う場合はWeb側で再発行してください）。",
            view=self,
        )


def _format_link_apply_preview(feature: str, term: str, diff_lines: list[str]) -> str:
    feature_names = {
        "all": "全体",
        "classes": "通常時間割",
        "notify": "通知設定",
        "overrides": "上書き設定",
        "gmail": "Gmail認証",
        "exam": "試験設定",
    }
    body = "\n".join(diff_lines) if diff_lines else "（変更はありません）"
    return (
        "連携キーを適用すると、次の変更があります。\n"
        f"■ 反映対象: {feature_names.get(feature, feature)} ({term})\n\n"
        f"{body}\n\n"
        "適用する場合はボタンを押してください（10分で自動キャンセル）。"
    )


async def _send_apply_result_dm(user, user_data, payload, gmail_ok: bool) -> None:
    feature = ((payload.get("_meta") or {}).get("feature") or "all").strip()
    term = normalize_term_key((payload.get("_meta") or {}).get("term"))
    feature_names = {
        "all": "全体",
        "classes": "通常時間割",
        "notify": "通知設定",
        "overrides": "上書き設定",
        "gmail": "Gmail認証",
        "exam": "試験設定",
    }
    gmail_code = (payload.get("gmail_auth_code") or "").strip()
    lines = [
        "【Webダッシュボード通知】",
        "Webダッシュボードからデータを反映しました。\n",
        f"■ 反映対象: {feature_names.get(feature, feature)} ({term})\n",
    ]

    if feature in ["all", "classes"]:
        classes = user_data.get("classes_by_term", {}).get(term, [])
        lines.append("【通常時間割 反映内容】")
        if classes:
            for cls in classes:
                day_name = WEEKDAYS[cls["day"]] if isinstance(cls.get("day"), int) and 0 <= cls["day"] < len(WEEKDAYS) else "不明"
                lines.append(f"・{day_name} {cls.get('period')}限: {cls.get('subject')} ({cls.get('room')})")
        else:
            lines.append("・登録されている授業はありません。")
        lines.append("")

    if feature in ["all", "overrides"]:
        day_ov = payload.get("day_overrides") or []
        room_ov = payload.get("room_overrides") or []
        lines.append("【時間割上書き設定 反映内容】")
        if day_ov or room_ov:
            if day_ov:
                lines.append("■ 日付上書き（休講・授業変更など）:")
                for d in day_ov:
                    lines.append(f"  ・{d.get('date')}: {d.get('weekday')}時間割")
            if room_ov:
                lines.append("■ 教室上書き:")
                for r in room_ov:
                    lines.append(f"  ・{r.get('date')} {r.get('period')}限: ➔ {r.get('room')}")
        else:
            lines.append("・上書き設定はありません。")
        lines.append("")

    if feature in ["all", "gmail"] or gmail_code:
        lines.append("【Gmail連携 反映内容】")
        if gmail_ok:
            lines.append("・Gmail認証連携を正常に完了しました！今後は定期的に Gmail を確認し、休講・授業変更のメールが届いたらDMでお知らせします。")
        elif gmail_code:
            lines.append("・Gmail認証連携に失敗しました。Web画面で認証URLの取得からやり直し、新しいキーを発行してください。")
        else:
            lines.append("・Gmail連携は今回更新されませんでした。")
        lines.append("")

    if feature in ["all", "exam"]:
        exams = user_data.get("exam_schedules_by_term", {}).get(term, [])
        lines.append("【試験時間割設定 反映内容】")
        if exams:
            for ex in exams:
                lines.append(f"・{ex.get('name')} ({ex.get('start')} 〜 {ex.get('end')})")
                for ec in ex.get("classes") or []:
                    day_name = WEEKDAYS[ec["day"]] if isinstance(ec.get("day"), int) and 0 <= ec["day"] < len(WEEKDAYS) else "不明"
                    lines.append(f"  └ {day_name} {ec.get('period')}限: {ec.get('subject')} ({ec.get('room')})")
        else:
            lines.append("・試験設定はありません。")
        lines.append("")

    await send_long_dm(user, "\n".join(lines))


web_group = app_commands.Group(name="web", description="Web登録データを取り込み")


def _enable_user_install_support(tree: app_commands.CommandTree) -> None:
    """Enable user-install + DM contexts for all top-level app commands."""
    if not _supports_app_command_context_features():
        return

    for cmd in tree.get_commands():
        try:
            app_commands.allowed_installs(guilds=True, users=True)(cmd)
        except Exception as e:
            logger.warning(
                "[WARN] allowed_installs の適用に失敗しました: command=%s error=%s",
                getattr(cmd, "qualified_name", getattr(cmd, "name", "unknown")),
                e,
            )
        try:
            app_commands.allowed_contexts(
                guilds=True,
                dms=True,
                private_channels=True,
            )(cmd)
        except Exception as e:
            logger.warning(
                "[WARN] allowed_contexts の適用に失敗しました: command=%s error=%s",
                getattr(cmd, "qualified_name", getattr(cmd, "name", "unknown")),
                e,
            )


@web_group.command(name="applykey", description="Webで発行した連携キーを取り込みます")
@app_commands.describe(key="Web画面で発行した連携キー")
async def web_applykey(interaction: discord.Interaction, key: str):
    await interaction.response.defer(ephemeral=True)
    user_id = interaction.user.id
    payload = consume_link_key((key or "").strip().upper())
    if not payload:
        await interaction.followup.send(
            "キーが無効、または有効期限切れです。Web側で再発行してください。",
            ephemeral=True,
        )
        return

    try:
        feature = ((payload.get("_meta") or {}).get("feature") or "all").strip()
        term = normalize_term_key((payload.get("_meta") or {}).get("term"))

        # その場では保存せず、まず差分を作って本人に確認してもらう
        user_data = load_user_data(user_id)
        new_data = _apply_web_payload_to_user_data(
            copy.deepcopy(dict(user_data)), payload
        )
        diff_lines = _build_apply_diff_lines(dict(user_data), new_data, payload)
        gmail_code = (payload.get("gmail_auth_code") or "").strip()
        if not diff_lines and not gmail_code:
            await interaction.followup.send(
                "このキーでは変更がありませんでした。データは変更されていません。",
                ephemeral=True,
            )
            return

        token = _stash_pending_link_apply(
            user_id, payload, _user_data_digest(user_data)
        )
        view = LinkApplyConfirmView(user_id, token)
        message = await interaction.followup.send(
            _format_link_apply_preview(feature, term, diff_lines),
            view=view,
            ephemeral=True,
        )
        view.message = message
    except Exception as e:
        logger.exception(f"[ERROR] web_applykey error: {e}")
        await interaction.followup.send(
            f"取り込み中にエラーが発生しました: {e}", ephemeral=True
        )

# --- Bot クラス ---
class ClassBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        # Let discord.py create its connector inside the running event loop.
        # Creating aiohttp.TCPConnector here can fail on newer aiohttp versions.
        super().__init__(command_prefix="!", intents=intents)
        # Ensure all app commands are user-installable and executable in DMs by default.
        if _supports_app_command_context_features():
            try:
                self.tree.allowed_installs = app_commands.AppInstallationType(
                    guild=True,
                    user=True,
                )
                self.tree.allowed_contexts = app_commands.AppCommandContext(
                    guild=True,
                    dm_channel=True,
                    private_channel=True,
                )
            except Exception as e:
                logger.warning(f"[WARN] tree default contexts/install 設定失敗: {e}")
        else:
            logger.info(
                "discord.py のバージョンが古いため、app command の install/context 設定はスキップします。"
            )

    async def setup_hook(self):
        global _discord_bot_instance
        _discord_bot_instance = self
        logger.info("[Bot] グローバルbotインスタンスが設定されました。")

        # builtinsにグローバル登録 (スレッド/インポート境界を越えた共有用)
        import builtins

        builtins._discord_bot = self
        logger.info("[Bot] builtins にBotインスタンスを登録しました。")

        # DM通知モジュールにもBotインスタンスを登録
        try:
            from web.api.notification import set_discord_bot

            set_discord_bot(self)
        except Exception as e:
            logger.error(f"[Bot] notificationへのBotインスタンス登録に失敗しました: {e}")

        # 起動時にロードするcogリスト
        target_cogs = [
            "cogs.class_cog",
            "cogs.exam_cog",
            "cogs.setting_cog",
            "cogs.notification_cog",
            "cogs.makeup_cog",
            "cogs.mail_cog",
        ]

        loaded_cogs = []
        failed_cogs = []

        for ext in target_cogs:
            try:
                await self.load_extension(ext)
                loaded_cogs.append(ext)
                logger.info(f"[INFO] 拡張機能のロード成功: {ext}")
            except Exception as e:
                failed_cogs.append((ext, str(e)))
                logger.exception(f"[ERROR] 拡張機能のロード失敗 ({ext}): {e}")

        # ロード結果のまとめログ出力
        logger.info("========================================")
        logger.info(
            f"[COG STATUS] 読み込み成功したCog ({len(loaded_cogs)}/{len(target_cogs)}): {loaded_cogs if loaded_cogs else 'なし'}"
        )
        logger.info(
            f"[COG STATUS] 読み込み失敗したCog ({len(failed_cogs)}/{len(target_cogs)}): {[f'{ext} (理由: {err})' for ext, err in failed_cogs] if failed_cogs else 'なし'}"
        )
        logger.info("========================================")

        self.tree.add_command(web_group)

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
                "• /class add weekday period subject room [exam]\n  → 授業を登録します（exam: 定期試験の有無。既定はあり＝試験時間割に自動追加）"
            )
            lines.append("• /class remove weekday period\n  → 授業を削除します")
            lines.append(
                "• /class list [term]\n  → 登録授業一覧をDMで受け取ります（term: 前期/後期）"
            )
            lines.append(
                "• /class table [term]\n  → 時間割表形式で表示します（term: 前期/後期）"
            )
            lines.append(
                "• /class setroom date period new_room\n  → 指定日の教室を変更します"
            )
            lines.append("• /class unsetroom target\n  → 教室変更を取り消します")
            lines.append(
                "• /class setday date new_weekday\n  → 指定日の曜日を変更します（代替時間割対応）"
            )
            lines.append("• /class unsetday date\n  → 曜日変更を取り消します\n")

            lines.append("===  補講・休講管理 ===")
            lines.append(
                "• /makeup add date period source [room]\n  → 指定日・時限に登録済みの授業を補講として追加します（元の授業の回数に加算）"
            )
            lines.append("• /makeup remove target\n  → 補講を削除します")
            lines.append("• /makeup list\n  → 補講一覧を表示します")
            lines.append(
                "• /cancel add date period\n  → 指定日の授業を休講にします（通知せず、回数にも数えません）"
            )
            lines.append("• /cancel remove target\n  → 休講を取り消します")
            lines.append("• /cancel list\n  → 休講一覧を表示します\n")

            lines.append("===  試験時間割（/exam） ===")
            lines.append(
                "• /exam create name start end\n  → 試験用時間割を作成します（定期試験ありの授業を自動追加）"
            )
            lines.append(
                "• /exam edit name [new_name] [start] [end]\n  → 試験時間割の名前・期間を変更します"
            )
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
                "• /exam set_time period time\n  → 試験期間中の時限開始時刻を設定します"
            )
            lines.append(
                "• /exam reset_time period\n  → 試験期間中の時限開始時刻を元に戻します（all で全時限）\n"
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
                "• /setting reset_period period\n  → 時限設定をデフォルトに戻します"
            )
            lines.append(
                "• /setting morning_time time\n  → 朝の授業一覧通知の時刻を設定します"
            )
            lines.append(
                "• /setting delete_data\n  → 保存されている自分のデータをすべて削除します\n"
            )

            lines.append("===  Gmail 認証 / 連携（/mail） ===")
            lines.append(
                "• /mail auth\n  → Gmail 認証を開始します（認証URLをDMで送信）"
            )
            lines.append(
                "• /mail setcode code\n  → 認証コードを入力して連携を完了します"
            )
            lines.append(
                "• /mail fetch\n  → Gmailから最新の休講・授業変更情報を取得してDMで表示します（確認ボタンを押すと時間割に反映されます）"
            )
            lines.append(
                "• /mail autoscan enabled\n  → 新着メールの自動確認（DM通知）を切り替えます（連携すると既定で有効）"
            )
            lines.append(
                "• /mail unlink\n  → Gmail 連携を解除し、認証情報を削除します\n"
            )

            lines.append("===  Web連携（/web） ===")
            lines.append(
                "• /web applykey key\n  → Web画面で登録した時間割・設定を取り込みます\n"
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

        # ユーザーインストール（User Install）とDM実行を許可
        _enable_user_install_support(self.tree)


bot = ClassBot()

# グローバルなbotインスタンス参照用
_discord_bot_instance: Optional[commands.Bot] = None


def get_discord_bot() -> Optional[commands.Bot]:
    return _discord_bot_instance

@bot.event
async def on_member_join(member: discord.Member):
    """
    新しくサーバーに参加したメンバーに使い方をDMで送信し、
    その後メンバーをサーバーから削除（キック）する。
    """
    try:
        # 使い方説明メッセージ
        welcome_msg = (
            "こんにちは！授業情報Botへようこそ。\n\n"
            "このBotはサーバーを窓口として、あなたの授業管理をサポートします。\n\n"
            "【ご利用の流れ】\n"
            "1. サーバーのテキストチャンネルで `/help` コマンドを実行\n"
            "2. ヘルプとコマンド一覧がDMで届きます\n"
            "3. 以降、すべてのやり取りはDMで行えます\n\n"
            "【主な機能】\n"
            "• /class add - 授業を登録\n"
            "• /class list - 登録授業一覧（DM）\n"
            "• /exam ... - 試験時間割管理\n"
            "• /notify ... - 通知設定\n"
            "• /web applykey - Web登録データの反映\n"
            "• その他多数\n\n"
            "コマンドは、サーバー内のテキストチャンネル、またはBotのDМで使用できます。\n"
            "詳細は /help を実行してご確認ください。"
        )
        await member.send(welcome_msg)
        logger.info(f"[INFO] 新メンバーへDM送信完了: {member.name} ({member.id})")
    except discord.Forbidden:
        logger.warning(
            f"[WARN] メンバーへのDM送信に失敗（DM受信拒否）: {member.name} ({member.id})"
        )
    except Exception as e:
        logger.exception(f"[ERROR] on_member_join中に例外発生: {e}")

    # メンバーをサーバーから削除
    try:
        await member.kick(reason="Welcome DM sent. Kicking per bot setup.")
        logger.info(f"[INFO] メンバーをキック: {member.name} ({member.id})")
    except discord.Forbidden:
        logger.error(
            f"[ERROR] メンバーのキックに失敗（権限不足）: {member.name} ({member.id})"
        )
    except Exception as e:
        logger.exception(f"[ERROR] メンバーキック中に例外発生: {e}")


@bot.event
async def on_ready():
    logger.info(f"Bot 起動完了: {bot.user}")
    logger.info(
        "[INFO] Intents: members=%s message_content=%s",
        bool(getattr(bot.intents, "members", False)),
        bool(getattr(bot.intents, "message_content", False)),
    )
    try:
        synced_global = await bot.tree.sync()
        logger.info(f"グローバルにコマンド同期: {len(synced_global)} 件")
    except Exception as e:
        logger.exception(f"グローバル同期失敗: {e}")

    # Optional: keep guild sync only when explicitly enabled for quick testing.
    guild = discord.Object(id=GUILD_ID) if GUILD_ID else None
    if not SYNC_GUILD_COMMANDS:
        logger.info("ギルド同期は無効です（SYNC_GUILD_COMMANDS=0）。")
        return

    try:
        if guild:
            synced_guild = await bot.tree.sync(guild=guild)
            logger.info(f"ギルドにコマンド同期: {[cmd.name for cmd in synced_guild]}")
        else:
            logger.info("GUILD_ID が設定されていません。ギルド同期をスキップします。")
    except Exception as e:
        logger.exception(f"ギルド同期失敗: {e}")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN が未設定です。.env を確認してください。")

    # Web API サーバーをスレッドで起動（Discord Bot と並行実行）
    web_port = int(os.getenv("WEB_PORT", "8000"))
    web_host = os.getenv("WEB_HOST", "0.0.0.0")
    tunnel_public_base_url = (
        (os.getenv("TUNNEL_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    )
    github_pages_url = (os.getenv("GITHUB_PAGES_URL") or "").strip().rstrip("/")
    logger.info(f"Web API サーバーを起動します: {web_host}:{web_port}")

    if tunnel_public_base_url:
        logger.info(f"[INFO] Tunnel URL: {tunnel_public_base_url}")
    if tunnel_public_base_url and github_pages_url:
        logger.info(f"[INFO] GitHub Pages URL: {github_pages_url}")

    try:
        start_web_server(host=web_host, port=web_port)
        logger.info("[INFO] Web API サーバーが起動しました")
    except Exception as e:
        logger.exception(f"[ERROR] Web API サーバー起動失敗: {e}")

    # Discord Bot を起動
    logger.info("[SSL] Python/OpenSSL: %s", ssl.OPENSSL_VERSION)
    logger.info(
        "[SSL] DISCORD_FORCE_IPV4=%s AIOHTTP_DNS_TTL=%s",
        (os.getenv("DISCORD_FORCE_IPV4") or "1"),
        (os.getenv("AIOHTTP_DNS_TTL") or "60"),
    )
    logger.info("[INFO] Discord Bot を起動します...")
    bot.run(DISCORD_TOKEN)
