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

async def setup(bot: commands.Bot):
    await bot.add_cog(ExamCog(bot))