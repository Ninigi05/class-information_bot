import os
import json
import re
import asyncio
import traceback
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, date
from utils import (
    load_user_data,
    save_user_data,
    send_dm,
    send_long_dm,
    WEEKDAYS,
    WEEKDAY_MAP,
    PERIOD_TO_TIME,
    DEFAULT_NOTIFY,
)

BASE_DIR = os.getcwd()
MORNING_MARK_FILE = os.path.join(BASE_DIR, "morning_sent.json")
logger = logging.getLogger(__name__)


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
        self._last_notif_minute = None
        self._notif_lock = asyncio.Lock()
        self.notification_loop.start()

    def cog_unload(self):
        self.notification_loop.cancel()

    # ------------------- autocomplete helpers -------------------

    async def subject_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        user_id = interaction.user.id
        data = load_user_data(user_id)
        subjects = []
        for c in data.get("classes", []) or []:
            s = str(c.get("subject", "")).strip()
            if s and current in s:
                subjects.append(s)
        for m in data.get("makeup_classes", []) or []:
            s = str(m.get("subject", "")).strip()
            if s and current in s:
                subjects.append(s)
        seen = set()
        choices = []
        for s in subjects:
            if s not in seen:
                seen.add(s)
                choices.append(app_commands.Choice(name=s, value=s))
                if len(choices) >= 25:
                    break
        return choices

    async def makeup_time_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        vals = list(PERIOD_TO_TIME.keys()) + list(set(PERIOD_TO_TIME.values()))
        choices = []
        for v in vals:
            if current in str(v):
                choices.append(app_commands.Choice(name=str(v), value=str(v)))
                if len(choices) >= 25:
                    break
        return choices

    # ------------------- notify group -------------------

    notify_group = app_commands.Group(
        name="notify", description="通知時刻や朝一覧の設定"
    )

    @notify_group.command(
        name="set_period", description="時限ごとの開始時刻を登録します"
    )
    @app_commands.describe(period="時限（例：1）", time="開始時刻（例：09:00）")
    async def notify_set_period(
        self, interaction: discord.Interaction, period: str, time: str
    ):
        if not re.match(r"^(2[0-3]|[01]?\d):[0-5]\d$", time):
            await interaction.response.send_message(
                "時刻は「HH:MM」の形式で入力してください（例：09:00）。", ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("period_overrides", {})[period] = time
        save_user_data(user_id, data)
        await interaction.response.send_message(
            f"{period}限の開始時刻を {time} に設定しました。", ephemeral=True
        )

    @notify_group.command(
        name="set", description="通知時刻（分前）を設定します（type: normal|exam）"
    )
    @app_commands.describe(
        type="normal または exam", first="1回目通知（分前）", second="2回目通知（分前）"
    )
    async def notify_set(
        self, interaction: discord.Interaction, type: str, first: int, second: int
    ):
        await interaction.response.defer(ephemeral=True)
        t = (type or "").lower()
        if t not in ("normal", "exam"):
            await interaction.followup.send(
                "type は 'normal' または 'exam' を指定してください。", ephemeral=True
            )
            return
        if first <= 0 or second <= 0:
            await interaction.followup.send(
                "分は正の整数で指定してください。", ephemeral=True
            )
            return
        if first < second:
            first, second = second, first
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("notify_settings", {}).setdefault(t, {})
        data["notify_settings"][t]["first"] = int(first)
        data["notify_settings"][t]["second"] = int(second)
        save_user_data(user_id, data)
        await send_dm(
            interaction.user,
            f" 通知設定を保存しました（{t}）：{first}分 / {second}分 前",
        )
        await interaction.followup.send("通知設定をDMで保存しました。", ephemeral=True)

    @notify_group.command(name="show", description="現在の通知設定を表示します（DM）")
    async def notify_show(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        user_notify = data.get("notify_settings", {}) or {}
        normal_cfg = user_notify.get("normal") or DEFAULT_NOTIFY["normal"]
        exam_cfg = user_notify.get("exam") or DEFAULT_NOTIFY["exam"]
        msg = (
            f"通知設定:\n"
            f"- 通常: {normal_cfg.get('first')}分 / {normal_cfg.get('second')}分 前\n"
            f"- 試験: {exam_cfg.get('first')}分 / {exam_cfg.get('second')}分 前\n"
        )
        await send_dm(interaction.user, msg)
        await interaction.followup.send("通知設定をDMで送信しました。", ephemeral=True)

    # ------------------- cancel group -------------------

    cancel_group = app_commands.Group(
        name="cancel", description="休講の手動登録 / 表示 / 削除"
    )

    @cancel_group.command(name="add", description="手動で休講情報を追加します")
    @app_commands.describe(date="休講日 (YYYY-MM-DD)", subject="科目名")
    @app_commands.autocomplete(subject=subject_autocomplete)
    async def cancel_add(
        self, interaction: discord.Interaction, date: str, subject: str
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except Exception:
            await interaction.followup.send(
                "日付形式が無効です。YYYY-MM-DD で指定してください。", ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("manual_cancellations", [])
        for c in data["manual_cancellations"]:
            if c.get("date") == date and c.get("subject") == subject:
                await interaction.followup.send(
                    "既に同一の休講が登録されています。", ephemeral=True
                )
                return
        data["manual_cancellations"].append({"date": date, "subject": subject})
        save_user_data(user_id, data)
        await send_dm(interaction.user, f" 手動休講を追加しました: {date} {subject}")
        await interaction.followup.send("休講登録をDMで送信しました。", ephemeral=True)

    @cancel_group.command(name="remove", description="手動で登録した休講を削除します")
    @app_commands.describe(date="休講日 (YYYY-MM-DD)", subject="科目名")
    @app_commands.autocomplete(subject=subject_autocomplete)
    async def cancel_remove(
        self, interaction: discord.Interaction, date: str, subject: str
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        if not data.get("manual_cancellations"):
            await interaction.followup.send(
                "手動休講は登録されていません。", ephemeral=True
            )
            return
        new_list = [
            c
            for c in data["manual_cancellations"]
            if not (c.get("date") == date and c.get("subject") == subject)
        ]
        if len(new_list) == len(data["manual_cancellations"]):
            await interaction.followup.send(
                "該当の休講が見つかりませんでした。", ephemeral=True
            )
            return
        data["manual_cancellations"] = new_list
        save_user_data(user_id, data)
        await send_dm(interaction.user, f" 手動休講を削除しました: {date} {subject}")
        await interaction.followup.send("削除結果をDMで送信しました。", ephemeral=True)

    @cancel_group.command(
        name="list", description="手動で登録した休講一覧を表示します（DM）"
    )
    async def cancel_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        manual = data.get("manual_cancellations", []) or []
        if not manual:
            await send_dm(interaction.user, "手動休講は登録されていません。")
            await interaction.followup.send(
                "DMを送信しました（休講なし）。", ephemeral=True
            )
            return
        lines = ["手動で登録した休講一覧:"]
        for c in sorted(manual, key=lambda x: x.get("date", "")):
            lines.append(f"{c.get('date')} : {c.get('subject')}")
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send("休講一覧をDMで送信しました。", ephemeral=True)

    # ------------------- makeup group -------------------

    makeup_group = app_commands.Group(
        name="makeup", description="補講（補講の追加/一覧/削除）"
    )

    @makeup_group.command(name="add", description="補講を追加します")
    @app_commands.describe(
        date="補講日 (YYYY-MM-DD)",
        time="開始時刻または時限（HH:MM or 2）",
        subject="科目名",
        room="教室",
    )
    @app_commands.autocomplete(
        time=makeup_time_autocomplete, subject=subject_autocomplete
    )
    async def makeup_add(
        self,
        interaction: discord.Interaction,
        date: str,
        time: str,
        subject: str,
        room: str,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except Exception:
            await interaction.followup.send(
                "日付形式が無効です。YYYY-MM-DD で指定してください。", ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("makeup_classes", [])
        for m in data["makeup_classes"]:
            if m.get("date") == date and m.get("time") == time:
                await interaction.followup.send(
                    "同じ日時ですでに補講が登録されています。", ephemeral=True
                )
                return
        data["makeup_classes"].append(
            {"date": date, "time": time, "subject": subject, "room": room}
        )
        save_user_data(user_id, data)
        await send_dm(
            interaction.user, f" 補講を登録しました: {date} {time} {subject} ({room})"
        )
        await interaction.followup.send("補講をDMで登録しました。", ephemeral=True)

    @makeup_group.command(name="remove", description="補講を削除します（日時指定）")
    @app_commands.describe(
        date="補講日 (YYYY-MM-DD)", time="開始時刻または時限（HH:MM or 2）"
    )
    @app_commands.autocomplete(time=makeup_time_autocomplete)
    async def makeup_remove(
        self, interaction: discord.Interaction, date: str, time: str
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        before = len(data.get("makeup_classes", []))
        data["makeup_classes"] = [
            m
            for m in data.get("makeup_classes", [])
            if not (m.get("date") == date and str(m.get("time")) == str(time))
        ]
        save_user_data(user_id, data)
        removed = before - len(data.get("makeup_classes", []))
        await send_dm(
            interaction.user, f" 補講を{removed}件削除しました: {date} {time}"
        )
        await interaction.followup.send("削除結果をDMで送信しました。", ephemeral=True)

    @makeup_group.command(name="list", description="補講一覧をDMで表示します")
    async def makeup_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        mak = data.get("makeup_classes", []) or []
        if not mak:
            await send_dm(interaction.user, "補講は登録されていません。")
            await interaction.followup.send(
                "DMを送信しました（補講なし）。", ephemeral=True
            )
            return
        lines = ["補講一覧:"]
        for m in sorted(mak, key=lambda x: (x.get("date", ""), x.get("time", ""))):
            lines.append(
                f"{m.get('date')} {m.get('time')} : {m.get('subject')} ({m.get('room')})"
            )
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send("補講一覧をDMで送信しました。", ephemeral=True)

    # ------------------- background notification task -------------------

    @tasks.loop(minutes=1)
    async def notification_loop(self):
        await self._do_notification_pass()

    @notification_loop.before_loop
    async def before_notification_loop(self):
        await self.bot.wait_until_ready()
        # catch-up: if between 08:00 and 12:00, run a fake 08:00 pass so any user
        # who did not yet receive the morning summary will receive it now.
        # Per-user idempotency is handled inside _do_notification_pass via the morning marker.
        now = datetime.now()
        if 8 <= now.hour < 12:
            try:
                await self._do_notification_pass(
                    now=now.replace(hour=8, minute=0, second=0, microsecond=0)
                )
            except Exception as e:
                logger.warning(f"[WARN] 起動時補完で例外: {e}")

    async def _do_notification_pass(self, now: datetime = None):
        if now is None:
            now = datetime.now()

        async with self._notif_lock:
            minute_key = now.strftime("%Y-%m-%d %H:%M")
            if self._last_notif_minute == minute_key:
                return
            self._last_notif_minute = minute_key

            now_minutes = now.hour * 60 + now.minute
            today_str = now.strftime("%Y-%m-%d")
            today_weekday = now.weekday()

            try:
                for filename in os.listdir(BASE_DIR):
                    if not filename.startswith("user_") or not filename.endswith(
                        ".json"
                    ):
                        continue
                    try:
                        user_id = int(filename.split("_")[1].split(".")[0])
                    except Exception:
                        continue

                    try:
                        user = await self.bot.fetch_user(user_id)
                    except discord.NotFound:
                        continue
                    except Exception as e:
                        logger.warning(f"[WARN] ユーザー取得失敗: {user_id} ({e})")
                        continue

                    data = load_user_data(user_id)
                    if not data:
                        continue

                    classes = data.get("classes", []) or []
                    exam_schedules = data.get("exam_schedules", []) or []
                    if (
                        not classes
                        and not data.get("makeup_classes")
                        and not exam_schedules
                    ):
                        continue

                    # check if today falls within an active exam schedule
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

                    # determine effective weekday (apply per-class date override if present)
                    target_weekday = today_weekday
                    for cls in classes:
                        if "overrides" in cls and today_str in cls["overrides"]:
                            target_weekday = cls["overrides"][today_str]
                            break

                    manual_cancellations = data.get("manual_cancellations", []) or []
                    user_notify = data.get("notify_settings", {}) or {}

                    if active_exam:
                        # --- exam period notification ---
                        exam_classes = active_exam.get("classes", []) or []
                        exam_classes_today = [
                            c for c in exam_classes if c.get("day") == target_weekday
                        ]
                        makeups_today = [
                            m
                            for m in data.get("makeup_classes", []) or []
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

                        # exam-specific period time overrides
                        period_overrides = data.get("exam_period_overrides", {}) or {}
                        # fall back to regular period overrides, then defaults
                        regular_overrides = data.get("period_overrides", {}) or {}

                        exam_cfg = user_notify.get("exam") or DEFAULT_NOTIFY["exam"]
                        try:
                            first_off = int(exam_cfg.get("first"))
                            second_off = int(exam_cfg.get("second"))
                        except Exception:
                            first_off = DEFAULT_NOTIFY["exam"]["first"]
                            second_off = DEFAULT_NOTIFY["exam"]["second"]
                        if first_off < second_off:
                            first_off, second_off = second_off, first_off
                        offsets = (first_off, second_off)

                        # per-class exam reminders
                        for cls in final_classes:
                            room = cls.get("room", "未設定")
                            if (
                                "room_overrides" in cls
                                and today_str in cls["room_overrides"]
                            ):
                                room = cls["room_overrides"][today_str]

                            p_key = str(cls.get("period"))
                            time_str = (
                                period_overrides.get(p_key)
                                or regular_overrides.get(p_key)
                                or cls.get("time")
                                or PERIOD_TO_TIME.get(p_key)
                            )
                            if not time_str:
                                continue
                            try:
                                h, m_val = map(int, time_str.split(":"))
                                class_minutes = h * 60 + m_val
                            except Exception:
                                continue

                            diff_minutes = class_minutes - now_minutes
                            if diff_minutes not in offsets:
                                continue

                            is_canceled = any(
                                c.get("date") == today_str
                                and c.get("subject") == cls.get("subject")
                                for c in manual_cancellations
                            )

                            msg = f"【試験期間】教室「{room}」で{diff_minutes}分後に授業「{cls.get('subject', '')}」が始まります"
                            if is_canceled:
                                msg += "\n※この授業は休講です（手動設定）"

                            try:
                                await send_dm(user, msg)
                            except Exception as e:
                                logger.error(
                                    f"[ERROR] DM送信失敗 (試験リマインダー) user={user_id}: {e}"
                                )

                        # morning summary (exam) at 08:00
                        if now.hour == 8 and now.minute == 0:
                            morning_marker = _load_morning_sent()
                            user_marks = morning_marker.get(str(user_id), {})
                            if user_marks.get(today_str):
                                continue

                            morning_marker.setdefault(str(user_id), {})[
                                today_str
                            ] = True
                            _save_morning_sent(morning_marker)

                            if final_classes:

                                def sort_key_exam(x):
                                    p = x.get("period")
                                    try:
                                        return int(p)
                                    except Exception:
                                        ts = (
                                            period_overrides.get(str(p))
                                            or regular_overrides.get(str(p))
                                            or x.get("time")
                                            or PERIOD_TO_TIME.get(str(p))
                                        )
                                        try:
                                            h2, m2 = map(int, ts.split(":"))
                                            return h2 * 60 + m2
                                        except Exception:
                                            return 99999

                                today_sorted = sorted(final_classes, key=sort_key_exam)
                                msg = f"【試験期間: {active_exam.get('name')}】本日の授業一覧（{WEEKDAYS[target_weekday]}）:\n"
                                for cls in today_sorted:
                                    room = cls.get("room", "未設定")
                                    if (
                                        "room_overrides" in cls
                                        and today_str in cls["room_overrides"]
                                    ):
                                        room = cls["room_overrides"][today_str]
                                    manual_hit = any(
                                        c.get("date") == today_str
                                        and c.get("subject") == cls.get("subject")
                                        for c in manual_cancellations
                                    )
                                    note = " ※休講（手動設定）" if manual_hit else ""
                                    period_display = cls.get(
                                        "period", cls.get("time", "?")
                                    )
                                    msg += f"{period_display}限 {cls.get('subject')} ({room}){note}\n"

                                try:
                                    await send_long_dm(user, msg)
                                except Exception as e:
                                    logger.error(
                                        f"[ERROR] DM送信失敗 (試験授業一覧) user={user_id}: {e}"
                                    )

                    else:
                        # --- normal period notification ---
                        today_classes = [
                            c for c in classes if c.get("day") == target_weekday
                        ]
                        makeups_today = [
                            m
                            for m in data.get("makeup_classes", []) or []
                            if m.get("date") == today_str
                        ]

                        # combine regular and makeup classes
                        final_classes = list(today_classes)
                        for m in makeups_today:
                            final_classes.append(
                                {
                                    "period": m.get("time", "?"),
                                    "time": m.get("time"),
                                    "subject": m.get("subject"),
                                    "room": m.get("room", "未設定"),
                                }
                            )

                        # resolve notification offsets
                        normal_cfg = (
                            user_notify.get("normal") or DEFAULT_NOTIFY["normal"]
                        )
                        try:
                            first_off = int(normal_cfg.get("first"))
                            second_off = int(normal_cfg.get("second"))
                        except Exception:
                            first_off = DEFAULT_NOTIFY["normal"]["first"]
                            second_off = DEFAULT_NOTIFY["normal"]["second"]
                        if first_off < second_off:
                            first_off, second_off = second_off, first_off
                        offsets = (first_off, second_off)

                        period_overrides = data.get("period_overrides", {}) or {}

                        # per-class reminder notifications
                        for cls in final_classes:
                            room = cls.get("room", "未設定")
                            if (
                                "room_overrides" in cls
                                and today_str in cls["room_overrides"]
                            ):
                                room = cls["room_overrides"][today_str]

                            p_key = str(cls.get("period"))
                            time_str = (
                                period_overrides.get(p_key)
                                or cls.get("time")
                                or PERIOD_TO_TIME.get(p_key)
                            )
                            if not time_str:
                                continue
                            try:
                                h, m_val = map(int, time_str.split(":"))
                                class_minutes = h * 60 + m_val
                            except Exception:
                                continue

                            diff_minutes = class_minutes - now_minutes
                            if diff_minutes not in offsets:
                                continue

                            is_canceled = any(
                                c.get("date") == today_str
                                and c.get("subject") == cls.get("subject")
                                for c in manual_cancellations
                            )

                            msg = f"教室「{room}」で{diff_minutes}分後に授業「{cls.get('subject', '')}」が始まります"
                            if is_canceled:
                                msg += "\n※この授業は休講です（手動設定）"

                            try:
                                await send_dm(user, msg)
                            except Exception as e:
                                logger.error(
                                    f"[ERROR] DM送信失敗 (リマインダー) user={user_id}: {e}"
                                )

                        # morning summary at 08:00
                        if now.hour == 8 and now.minute == 0:
                            morning_marker = _load_morning_sent()
                            user_marks = morning_marker.get(str(user_id), {})
                            if user_marks.get(today_str):
                                continue

                            morning_marker.setdefault(str(user_id), {})[
                                today_str
                            ] = True
                            _save_morning_sent(morning_marker)

                            if final_classes:

                                def sort_key(x):
                                    p = x.get("period")
                                    try:
                                        return int(p)
                                    except Exception:
                                        ts = (
                                            period_overrides.get(str(p))
                                            or x.get("time")
                                            or PERIOD_TO_TIME.get(str(p))
                                        )
                                        try:
                                            h2, m2 = map(int, ts.split(":"))
                                            return h2 * 60 + m2
                                        except Exception:
                                            return 99999

                                today_sorted = sorted(final_classes, key=sort_key)
                                msg = f"本日の授業一覧（{WEEKDAYS[target_weekday]}）:\n"
                                for cls in today_sorted:
                                    room = cls.get("room", "未設定")
                                    if (
                                        "room_overrides" in cls
                                        and today_str in cls["room_overrides"]
                                    ):
                                        room = cls["room_overrides"][today_str]
                                    manual_hit = any(
                                        c.get("date") == today_str
                                        and c.get("subject") == cls.get("subject")
                                        for c in manual_cancellations
                                    )
                                    note = " ※休講（手動設定）" if manual_hit else ""
                                    period_display = cls.get(
                                        "period", cls.get("time", "?")
                                    )
                                    msg += f"{period_display}限 {cls.get('subject')} ({room}){note}\n"

                                try:
                                    await send_long_dm(user, msg)
                                except Exception as e:
                                    logger.error(
                                        f"[ERROR] DM送信失敗 (授業一覧) user={user_id}: {e}"
                                    )

            except Exception as e:
                logger.exception(f"[ERROR] _do_notification_pass 中の例外: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(NotificationCog(bot))
