import discord
from discord import app_commands
from discord.ext import commands
import re
from utils import load_user_data, save_user_data, send_dm
from datetime import datetime

class ExamCog(commands.GroupCog, name="exam"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def exam_name_autocomplete(self, interaction: discord.Interaction, current: str):
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

    @app_commands.command(name="set_time", descriotion="試験の各時限開始時間を登録します")
    @app_commands.describe(period="設定する時限（例：１）", time = "開始時刻（例：09:00）")
    async def set_exam_period_time(self, interaction: discord.Interaction, period: str, time: str):
        if not re.match(r"^\d{1,2}:\d{2}$",time):
            await interaction.response.send_message("時刻は「HH:MM」の形式で入力してください（例：09:00）。",ephemeral=True)
            return

        user_id = interaction.user.id
        data = load_user_data(user_id)

        if not re.match(r"^(2[0-3]|[01]?\d):[0-5]\d$",time):
            await interaction.response.send_message("時刻は0～23時：0～59分で入力して下さい",ephemeral = True)
            await interaction.responese.send_message("検証したい気持ちは大いに認めます。ほかのバグを見つけたら管理者に報告してください",epehemeral = True)
            return

        if "exam_period_overrides" not in data:
            data["exam_period_overrides"] = {}

        data["exam_period_overrides"][period] = time
        save_user_data(user_id, data)

        await interaction.response.send_message(f"試験時の{period}限の開始時刻を{time}に設定しました。",ephemeral = True)

    @app_commands.command(name="create", description="試験用時間割を作成します（名前・期間）")
    @app_commands.describe(name="時間割名", start="開始日 (YYYY-MM-DD)", end="終了日 (YYYY-MM-DD)")
    async def exam_create(self, interaction: discord.Interaction, name: str, start: str, end: str):
        await interaction.response.defer(ephemeral=True)
        try:
            sd = datetime.strptime(start, "%Y-%m-%d").date()
            ed = datetime.strptime(end, "%Y-%m-%d").date()
        except Exception:
            await interaction.followup.send("日付形式が無効です。YYYY-MM-DD で指定してください。", ephemeral=True)
            return
        if sd > ed:
            await interaction.followup.send("開始日は終了日より前にしてください。", ephemeral=True)
            return
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("exam_schedules", [])
        for s in data["exam_schedules"]:
            if s.get("name") == name:
                await interaction.followup.send("同名の試験時間割が既に存在します。", ephemeral=True)
                return
        data["exam_schedules"].append({"name": name, "start": start, "end": end, "classes": []})
        save_user_data(user_id, data)
        await send_dm(interaction.user, f" 試験時間割「{name}」を作成しました: {start} ～ {end}")
        await interaction.followup.send("試験時間割をDMで作成しました。", ephemeral=True)

    @app_commands.command(name="delete", description="指定した試験時間割を削除します")
    @app_commands.describe(name="削除する時間割名")
    @app_commands.autocomplete(name=exam_name_autocomplete)
    async def exam_delete(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        before = len(data.get("exam_schedules", []) or [])
        data["exam_schedules"] = [s for s in data.get("exam_schedules", []) if s.get("name") != name]
        save_user_data(user_id, data)
        removed = before - len(data.get("exam_schedules", []) or [])
        await send_dm(interaction.user, f"🗑️ 試験時間割「{name}」を削除しました（{removed}件）。")
        await interaction.followup.send("削除結果をDMで送信しました。", ephemeral=True)

    @app_commands.command(name="list", description="登録済み試験時間割の一覧をDMで表示します")
    async def exam_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        schedules = data.get("exam_schedules", []) or []
        if not schedules:
            await send_dm(interaction.user, "試験時間割は登録されていません。")
            await interaction.followup.send("DMを送信しました（試験時間割なし）。", ephemeral=True)
            return
        lines = [" 登録済み試験時間割:"]
        for s in sorted(schedules, key=lambda x: x.get("start", "")):
            lines.append(f"- {s.get('name')} : {s.get('start')} ～ {s.get('end')} ({len(s.get('classes', []))}件)")
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send("試験時間割一覧をDMで送信しました。", ephemeral=True)

    @app_commands.command(name="show", description="指定した試験時間割の中身を表示します")
    @app_commands.describe(name="表示する時間割名")
    @app_commands.autocomplete(name=exam_name_autocomplete)
    async def exam_show(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        schedules = data.get("exam_schedules", []) or []
        target = next((s for s in schedules if s.get("name") == name), None)
        if not target:
            await interaction.followup.send("該当の時間割が見つかりませんでした。", ephemeral=True)
            return
        classes = target.get("classes", []) or []
        if not classes:
            await send_dm(interaction.user, f"試験時間割「{name}」には授業が登録されていません。")
            await interaction.followup.send("DMを送信しました（授業なし）。", ephemeral=True)
            return
        lines = [f" 試験時間割「{name}」の授業:"]
        classes_sorted = sorted(classes, key=lambda x: (x.get("day", 0), int(x.get("period", 0) if str(x.get("period")).isdigit() else 999)))
        for c in classes_sorted:
            wd = WEEKDAYS[int(c.get("day"))] if c.get("day") is not None else "不明曜日"
            pd = c.get("period") or c.get("time") or "?"
            lines.append(f"{wd} {pd}限 {c.get('subject','')} ({c.get('room','未設定')})")
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send("試験時間割をDMで送信しました。", ephemeral=True)

    @app_commands.command(name="addclass", description="試験時間割に授業を追加します")
    @app_commands.describe(name="時間割名", weekday="曜日", period="時限", subject="科目名", room="教室", time="（任意）開始時刻 HH:MM")
    @app_commands.autocomplete(name=exam_name_autocomplete, weekday=weekday_autocomplete, period=period_autocomplete, subject=subject_autocomplete, room=room_autocomplete)
    async def exam_addclass(self, interaction: discord.Interaction, name: str, weekday: str, period: str, subject: str, room: str, time: str = None):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        schedules = data.get("exam_schedules", []) or []
        target = next((s for s in schedules if s.get("name") == name), None)
        if not target:
            await interaction.followup.send("該当の時間割が見つかりませんでした。", ephemeral=True)
            return
        day_idx = WEEKDAY_MAP.get(weekday)
        time_val = time or PERIOD_TO_TIME.get(period)
        entry = {"day": day_idx, "period": period, "time": time_val, "subject": subject, "room": room}
        target.setdefault("classes", []).append(entry)
        save_user_data(user_id, data)
        await send_dm(interaction.user, f" 試験時間割「{name}」に授業を追加しました: {weekday} {period}限 {subject} ({room})")
        await interaction.followup.send("試験時間割に授業を追加しました（DM送付）。", ephemeral=True)

    @app_commands.command(name="removeclass", description="試験時間割から授業を削除します（曜日＋時限で指定）")
    @app_commands.describe(name="時間割名", weekday="曜日", period="時限")
    @app_commands.autocomplete(name=exam_name_autocomplete, weekday=weekday_autocomplete, period=period_autocomplete)
    async def exam_removeclass(self, interaction: discord.Interaction, name: str, weekday: str, period: str):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        schedules = data.get("exam_schedules", []) or []
        target = next((s for s in schedules if s.get("name") == name), None)
        if not target:
            await interaction.followup.send("該当の時間割が見つかりませんでした。", ephemeral=True)
            return
        day_idx = WEEKDAY_MAP.get(weekday)
        before = len(target.get("classes", []))
        target["classes"] = [c for c in target.get("classes", []) if not (c.get("day") == day_idx and str(c.get("period")) == str(period))]
        save_user_data(user_id, data)
        deleted = before - len(target.get("classes", []))
        await send_dm(interaction.user, f" 試験時間割「{name}」から {weekday} {period}限 を削除しました（{deleted}件）。")
        await interaction.followup.send("削除結果をDMで送信しました。", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ExamCog(bot))