import discord
from discord import app_commands
from discord.ext import commands
import re
from utils import load_user_data, save_user_data

class SettingCog(commands.GroupCog, name = "setting"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(name = "period_time", description = "各時限の開始時刻を登録します")
    async def set_period_time(self, interaction: discord.Interaction, period: str, time: str):
        if not re.match(r"^\d{1,2}:\d{2}$", time):
            await interaction.response.send_message("時刻形式が不正です。", ephemeral = True)
            return
        
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("period_overrides", {})[period] = time
        save_user_data(user_id, data)

        await interaction.response.send_message(f"{period}限を{time}に設定しました。", ephemeral = True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SettingCog(bot))