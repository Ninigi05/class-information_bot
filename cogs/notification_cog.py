import os
import json
import re
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Optional
from utils import (
    load_user_data,
    save_user_data,
    get_user_data_mtime,
    send_dm,
    send_long_dm,
    get_current_term,
    WEEKDAYS,
    WEEKDAY_MAP,
    PERIOD_TO_TIME,
    DEFAULT_NOTIFY,
)

BASE_DIR = os.getcwd()
MORNING_MARK_FILE = os.path.join(BASE_DIR, "morning_sent.json")
logger = logging.getLogger(__name__)
APP_TIMEZONE = (os.getenv("APP_TIMEZONE") or "Asia/Tokyo").strip()
GRACE_WINDOW_SECONDS = int(os.getenv("NOTIF_GRACE_WINDOW", "300"))


def _now_local() -> datetime:
    try:
        return datetime.now(ZoneInfo(APP_TIMEZONE))
    except Exception:
        return datetime.now()


def _is_user_morning_time(now: datetime, data: dict) -> bool:
    t = str(data.get("morning_notice_time", "08:00") or "08:00").strip()
    try:
        hh, mm = [int(x) for x in t.split(":", 1)]
    except Exception:
        hh, mm = 8, 0
    return now.hour == hh and now.minute == mm


def _load_morning_sent():
    try:
        with open(MORNING_MARK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_morning_sent(d):
    try:
        with open(MORNING_MARK_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[WARN] morning_sent 保存失敗: {e}")


class NotificationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_notif_key = None
        self._sent_reminders: dict[int, set] = {}
        self._notif_lock = asyncio.Lock()
        self.notification_loop.start()

    def cog_unload(self):
        self.notification_loop.cancel()

    @tasks.loop(seconds=1)
    async def notification_loop(self):
        await self._do_notification_pass()

    @notification_loop.before_loop
    async def before_notification_loop(self):
        await self.bot.wait_until_ready()
        now = _now_local()
        if 8 <= now.hour < 12:
            try:
                await self._do_notification_pass(
                    now=now.replace(hour=8, minute=0, second=0, microsecond=0)
                )
            except Exception as e:
                logger.warning(f"[WARN] 起動時補完で例外: {e}")

    async def _do_notification_pass(self, now: Optional[datetime] = None):
        if now is None:
            now = _now_local()

        async with self._notif_lock:
            sec_key = now.strftime("%Y-%m-%d %H:%M:%S")
            if self._last_notif_key == sec_key:
                return
            self._last_notif_key = sec_key

            now_seconds = now.hour * 3600 + now.minute * 60 + now.second
            today_str = now.strftime("%Y-%m-%d")
            today_weekday = now.weekday()

            for filename in os.listdir(BASE_DIR):
                if not filename.startswith("user_") or not filename.endswith(".json"):
                    continue

                try:
                    user_id = int(filename.split("_")[1].split(".")[0])
                    user = self.bot.get_user(user_id)
                    if user is None:
                        user = await self.bot.fetch_user(user_id)
                except Exception:
                    continue

                data = load_user_data(user_id)
                if not data:
                    continue

                term = get_current_term()
                term_ranges = data.get("term_ranges", {}) or {}
                term_range = term_ranges.get(term)
                if term_range:
                    try:
                        s_dt = datetime.fromisoformat(term_range["start"]).date()
                        e_dt = datetime.fromisoformat(term_range["end"]).date()
                        if not (s_dt <= now.date() <= e_dt):
                            continue  # 通知期間外
                    except Exception:
                        pass

                classes = data.get("classes_by_term", {}).get(term, []) or []
                exam_schedules = (
                    data.get("exam_schedules_by_term", {}).get(term, []) or []
                )

                # 休講・上書き設定の読み込み
                manual_cancellations = data.get("manual_cancellations", []) or []
                user_notify = data.get("notify_settings", {}) or {}

                # 試験期間チェック
                active_exam = None
                for sched in exam_schedules:
                    try:
                        s_date = date.fromisoformat(sched.get("start", ""))
                        e_date = date.fromisoformat(sched.get("end", ""))
                        if s_date <= date.fromisoformat(today_str) <= e_date:
                            active_exam = sched
                            break
                    except Exception:
                        continue

                # 曜日上書きの適用
                target_weekday = today_weekday
                for cls_item in classes:
                    if "overrides" in cls_item and today_str in cls_item["overrides"]:
                        target_weekday = cls_item["overrides"][today_str]
                        break

                if active_exam:
                    # --- 試験期間通知 ---
                    exam_classes_today = [
                        c
                        for c in active_exam.get("classes", [])
                        if c.get("day") == target_weekday
                    ]
                    makeups_today = [
                        m
                        for m in data.get("makeup_classes", [])
                        if m.get("date") == today_str
                    ]
                    final_classes = list(exam_classes_today)
                    for m in makeups_today:
                        final_classes.append(
                            {
                                "period": m.get("time", "?"),
                                "time": m.get("time"),
                                "subject": m.get("subject"),
                                "room": m.get("room", "未設定"),
                            }
                        )

                    exam_cfg = user_notify.get("exam") or DEFAULT_NOTIFY["exam"]
                    offsets = (
                        int(exam_cfg.get("first", 30)) * 60,
                        int(exam_cfg.get("second", 15)) * 60,
                    )

                    for cls_item in final_classes:
                        p_key = str(cls_item.get("period"))
                        time_str = (
                            data.get("exam_period_overrides", {}).get(p_key)
                            or data.get("period_overrides", {}).get(p_key)
                            or cls_item.get("time")
                            or PERIOD_TO_TIME.get(p_key)
                        )
                        if not time_str:
                            continue

                        try:
                            h, m_val = map(int, time_str.split(":")[:2])
                            class_seconds = h * 3600 + m_val * 60
                        except Exception:
                            continue

                        for off in offsets:
                            target = class_seconds - off
                            if (
                                now_seconds >= target
                                and (now_seconds - target) <= GRACE_WINDOW_SECONDS
                            ):
                                rem_key = f"{today_str}|{p_key}|{cls_item.get('subject')}|{off}"
                                if rem_key not in self._sent_reminders.setdefault(
                                    user_id, set()
                                ):
                                    username = user.display_name
                                    room = cls_item.get("room", "未設定")
                                    msg = f"【試験期間】{username}さん、教室「{room}」で{off//60}分後に授業「{cls_item.get('subject')}」が始まります"
                                    await send_dm(user, msg)
                                    self._sent_reminders[user_id].add(rem_key)

                    # 朝の一覧
                    if _is_user_morning_time(now, data):
                        morning_marker = _load_morning_sent()
                        if not morning_marker.get(str(user_id), {}).get(today_str):
                            username = user.display_name
                            msg = f"【試験期間: {active_exam.get('name')}】{username}さん、本日の授業一覧（{WEEKDAYS[target_weekday]}）です:\n"
                            for cls_item in sorted(
                                final_classes, key=lambda x: str(x.get("period"))
                            ):
                                msg += f"{cls_item.get('period')}限 {cls_item.get('subject')} ({cls_item.get('room', '未設定')})\n"
                            await send_long_dm(user, msg)
                            morning_marker.setdefault(str(user_id), {})[
                                today_str
                            ] = True
                            _save_morning_sent(morning_marker)

                else:
                    # --- 通常通知 ---
                    today_classes = [
                        c for c in classes if c.get("day") == target_weekday
                    ]
                    makeups_today = [
                        m
                        for m in data.get("makeup_classes", [])
                        if m.get("date") == today_str
                    ]
                    final_classes = list(today_classes)
                    for m in makeups_today:
                        m["_is_makeup"] = True
                        final_classes.append(m)

                    normal_cfg = user_notify.get("normal") or DEFAULT_NOTIFY["normal"]
                    offsets = (
                        int(normal_cfg.get("first", 15)) * 60,
                        int(normal_cfg.get("second", 5)) * 60,
                    )

                    for cls_item in final_classes:
                        p_key = str(cls_item.get("period"))
                        time_str = (
                            data.get("period_overrides", {}).get(p_key)
                            or cls_item.get("time")
                            or PERIOD_TO_TIME.get(p_key)
                        )
                        if not time_str:
                            continue

                        try:
                            h, m_val = map(int, time_str.split(":")[:2])
                            class_seconds = h * 3600 + m_val * 60
                        except Exception:
                            continue

                        for off in offsets:
                            target = class_seconds - off
                            if (
                                now_seconds >= target
                                and (now_seconds - target) <= GRACE_WINDOW_SECONDS
                            ):
                                rem_key = f"{today_str}|{p_key}|{cls_item.get('subject')}|{off}"
                                if rem_key not in self._sent_reminders.setdefault(
                                    user_id, set()
                                ):
                                    username = user.display_name
                                    room = cls_item.get("room", "未設定")
                                    msg = f"{username}さん、教室「{room}」で{off//60}分後に授業「{cls_item.get('subject')}」が始まります"
                                    await send_dm(user, msg)
                                    self._sent_reminders[user_id].add(rem_key)

                                    # 授業回数カウントと自動削除 (2回目の通知時にカウント)
                                    if off == offsets[-1]:
                                        class_counts = (
                                            data.get("class_attendance_count", {}) or {}
                                        )
                                        term_counts = class_counts.setdefault(term, {})
                                        subj_key = f"{cls_item.get('day')}|{cls_item.get('period')}|{cls_item.get('subject')}"
                                        current_count = term_counts.get(subj_key, 0) + 1
                                        term_counts[subj_key] = current_count
                                        data["class_attendance_count"] = class_counts

                                        # 目標回数に達したかチェック
                                        target_count = data.get(
                                            "class_count_targets", {}
                                        ).get(term)
                                        if (
                                            target_count
                                            and current_count >= target_count
                                        ):
                                            # 自動削除
                                            new_classes = [
                                                c
                                                for c in classes
                                                if not (
                                                    c.get("day") == cls_item.get("day")
                                                    and c.get("period")
                                                    == cls_item.get("period")
                                                    and c.get("subject")
                                                    == cls_item.get("subject")
                                                )
                                            ]
                                            data.setdefault("classes_by_term", {})[
                                                term
                                            ] = new_classes
                                            await send_dm(
                                                user,
                                                f"授業「{cls_item.get('subject')}」の通知回数が設定された {target_count} 回に達したため、この授業のデータを削除し通知を終了しました。",
                                            )
                                        save_user_data(user_id, data)

                    # 朝の一覧
                    if _is_user_morning_time(now, data):
                        morning_marker = _load_morning_sent()
                        if not morning_marker.get(str(user_id), {}).get(today_str):
                            username = user.display_name
                            msg = f"{username}さん、本日の授業一覧（{WEEKDAYS[target_weekday]}）です:\n"
                            for cls_item in sorted(
                                final_classes, key=lambda x: str(x.get("period"))
                            ):
                                msg += f"{cls_item.get('period')}限 {cls_item.get('subject')} ({cls_item.get('room', '未設定')})\n"
                            await send_long_dm(user, msg)
                            morning_marker.setdefault(str(user_id), {})[
                                today_str
                            ] = True
                            _save_morning_sent(morning_marker)


class NotificationSettingsCog(commands.GroupCog, name="notify"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="set", description="通知タイミングを設定します")
    async def notify_set(
        self,
        interaction: discord.Interaction,
        notify_type: str,
        first: int,
        second: int,
    ):
        if notify_type not in ["normal", "exam"]:
            await interaction.response.send_message(
                "タイプは 'normal' または 'exam' を指定してください。", ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("notify_settings", {})[notify_type] = {
            "first": first,
            "second": second,
        }
        save_user_data(user_id, data)
        await interaction.response.send_message(
            f"{notify_type} 通知を {first}分前、{second}分前に設定しました。",
            ephemeral=True,
        )

    @app_commands.command(name="show", description="現在の通知設定を表示します")
    async def notify_show(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        data = load_user_data(user_id)
        settings = data.get("notify_settings", {})
        await interaction.response.send_message(
            f"現在の通知設定:\n{settings}", ephemeral=True
        )

    @app_commands.command(name="set_period", description="時限の開始時刻を設定します")
    async def set_period_time(
        self, interaction: discord.Interaction, period: str, time: str
    ):
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("period_overrides", {})[period] = time
        save_user_data(user_id, data)
        await interaction.response.send_message(
            f"{period}限の開始時刻を {time} に設定しました。", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(NotificationCog(bot))
    await bot.add_cog(NotificationSettingsCog(bot))
