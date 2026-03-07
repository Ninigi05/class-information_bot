import os
import json
import re
import traceback
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from utils import load_user_data, save_user_data, send_dm, send_long_dm

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

DEFAULT_NOTIFY = {
    "normal": {"first": 15, "second": 10},
    "exam": {"first": 30, "second": 25}
}



class NotificationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------- autocomplete helpers -------------------

    async def subject_autocomplete(self, interaction: discord.Interaction, current: str):
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

    async def makeup_time_autocomplete(self, interaction: discord.Interaction, current: str):
        vals = list(PERIOD_TO_TIME.keys()) + list(set(PERIOD_TO_TIME.values()))
        choices = []
        for v in vals:
            if current in str(v):
                choices.append(app_commands.Choice(name=str(v), value=str(v)))
                if len(choices) >= 25:
                    break
        return choices

    # ------------------- notify group -------------------

    notify_group = app_commands.Group(name="notify", description="通知時刻や朝一覧の設定")

    @notify_group.command(name="set_period", description="時限ごとの開始時刻を登録します")
    @app_commands.describe(period="時限（例：1）", time="開始時刻（例：09:00）")
    async def notify_set_period(self, interaction: discord.Interaction, period: str, time: str):
        if not re.match(r"^(2[0-3]|[01]?\d):[0-5]\d$", time):
            await interaction.response.send_message(
                "時刻は「HH:MM」の形式で入力してください（例：09:00）。",
                ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("period_overrides", {})[period] = time
        save_user_data(user_id, data)
        await interaction.response.send_message(f"{period}限の開始時刻を {time} に設定しました。", ephemeral=True)

    @notify_group.command(name="set", description="通知時刻（分前）を設定します（type: normal|exam）")
    @app_commands.describe(type="normal または exam", first="1回目通知（分前）", second="2回目通知（分前）")
    async def notify_set(self, interaction: discord.Interaction, type: str, first: int, second: int):
        await interaction.response.defer(ephemeral=True)
        t = (type or "").lower()
        if t not in ("normal", "exam"):
            await interaction.followup.send("type は 'normal' または 'exam' を指定してください。", ephemeral=True)
            return
        if first <= 0 or second <= 0:
            await interaction.followup.send("分は正の整数で指定してください。", ephemeral=True)
            return
        if first < second:
            first, second = second, first
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("notify_settings", {}).setdefault(t, {})
        data["notify_settings"][t]["first"] = int(first)
        data["notify_settings"][t]["second"] = int(second)
        save_user_data(user_id, data)
        await send_dm(interaction.user, f" 通知設定を保存しました（{t}）：{first}分 / {second}分 前")
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

    cancel_group = app_commands.Group(name="cancel", description="休講の手動登録 / 表示 / 削除")

    @cancel_group.command(name="add", description="手動で休講情報を追加します")
    @app_commands.describe(date="休講日 (YYYY-MM-DD)", subject="科目名")
    @app_commands.autocomplete(subject=subject_autocomplete)
    async def cancel_add(self, interaction: discord.Interaction, date: str, subject: str):
        await interaction.response.defer(ephemeral=True)
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except Exception:
            await interaction.followup.send("日付形式が無効です。YYYY-MM-DD で指定してください。", ephemeral=True)
            return
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("manual_cancellations", [])
        for c in data["manual_cancellations"]:
            if c.get("date") == date and c.get("subject") == subject:
                await interaction.followup.send("既に同一の休講が登録されています。", ephemeral=True)
                return
        data["manual_cancellations"].append({"date": date, "subject": subject})
        save_user_data(user_id, data)
        await send_dm(interaction.user, f" 手動休講を追加しました: {date} {subject}")
        await interaction.followup.send("休講登録をDMで送信しました。", ephemeral=True)

    @cancel_group.command(name="remove", description="手動で登録した休講を削除します")
    @app_commands.describe(date="休講日 (YYYY-MM-DD)", subject="科目名")
    @app_commands.autocomplete(subject=subject_autocomplete)
    async def cancel_remove(self, interaction: discord.Interaction, date: str, subject: str):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        if not data.get("manual_cancellations"):
            await interaction.followup.send("手動休講は登録されていません。", ephemeral=True)
            return
        new_list = [c for c in data["manual_cancellations"] if not (c.get("date") == date and c.get("subject") == subject)]
        if len(new_list) == len(data["manual_cancellations"]):
            await interaction.followup.send("該当の休講が見つかりませんでした。", ephemeral=True)
            return
        data["manual_cancellations"] = new_list
        save_user_data(user_id, data)
        await send_dm(interaction.user, f" 手動休講を削除しました: {date} {subject}")
        await interaction.followup.send("削除結果をDMで送信しました。", ephemeral=True)

    @cancel_group.command(name="list", description="手動で登録した休講一覧を表示します（DM）")
    async def cancel_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        manual = data.get("manual_cancellations", []) or []
        if not manual:
            await send_dm(interaction.user, "手動休講は登録されていません。")
            await interaction.followup.send("DMを送信しました（休講なし）。", ephemeral=True)
            return
        lines = ["手動で登録した休講一覧:"]
        for c in sorted(manual, key=lambda x: x.get("date", "")):
            lines.append(f"{c.get('date')} : {c.get('subject')}")
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send("休講一覧をDMで送信しました。", ephemeral=True)

    # ------------------- makeup group -------------------

    makeup_group = app_commands.Group(name="makeup", description="補講（補講の追加/一覧/削除）")

    @makeup_group.command(name="add", description="補講を追加します")
    @app_commands.describe(date="補講日 (YYYY-MM-DD)", time="開始時刻または時限（HH:MM or 2）", subject="科目名", room="教室")
    @app_commands.autocomplete(time=makeup_time_autocomplete, subject=subject_autocomplete)
    async def makeup_add(self, interaction: discord.Interaction, date: str, time: str, subject: str, room: str):
        await interaction.response.defer(ephemeral=True)
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except Exception:
            await interaction.followup.send("日付形式が無効です。YYYY-MM-DD で指定してください。", ephemeral=True)
            return
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("makeup_classes", [])
        for m in data["makeup_classes"]:
            if m.get("date") == date and m.get("time") == time:
                await interaction.followup.send("同じ日時ですでに補講が登録されています。", ephemeral=True)
                return
        data["makeup_classes"].append({"date": date, "time": time, "subject": subject, "room": room})
        save_user_data(user_id, data)
        await send_dm(interaction.user, f" 補講を登録しました: {date} {time} {subject} ({room})")
        await interaction.followup.send("補講をDMで登録しました。", ephemeral=True)

    @makeup_group.command(name="remove", description="補講を削除します（日時指定）")
    @app_commands.describe(date="補講日 (YYYY-MM-DD)", time="開始時刻または時限（HH:MM or 2）")
    @app_commands.autocomplete(time=makeup_time_autocomplete)
    async def makeup_remove(self, interaction: discord.Interaction, date: str, time: str):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        before = len(data.get("makeup_classes", []))
        data["makeup_classes"] = [
            m for m in data.get("makeup_classes", [])
            if not (m.get("date") == date and str(m.get("time")) == str(time))
        ]
        save_user_data(user_id, data)
        removed = before - len(data.get("makeup_classes", []))
        await send_dm(interaction.user, f" 補講を{removed}件削除しました: {date} {time}")
        await interaction.followup.send("削除結果をDMで送信しました。", ephemeral=True)

    @makeup_group.command(name="list", description="補講一覧をDMで表示します")
    async def makeup_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        mak = data.get("makeup_classes", []) or []
        if not mak:
            await send_dm(interaction.user, "補講は登録されていません。")
            await interaction.followup.send("DMを送信しました（補講なし）。", ephemeral=True)
            return
        lines = ["補講一覧:"]
        for m in sorted(mak, key=lambda x: (x.get("date", ""), x.get("time", ""))):
            lines.append(f"{m.get('date')} {m.get('time')} : {m.get('subject')} ({m.get('room')})")
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send("補講一覧をDMで送信しました。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(NotificationCog(bot))
