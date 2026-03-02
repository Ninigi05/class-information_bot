import discord
from discord import app_commands
from discord.ext import commands
import re
from utils import load_user_data, save_user_data

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

async def setup(bot: commands.Bot):
    await bot.add_cog(ExamCog(bot))