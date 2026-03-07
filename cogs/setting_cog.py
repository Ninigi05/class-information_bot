import discord
from discord import app_commands
from discord.ext import commands
import re
from utils import load_user_data, save_user_data

PERIOD_TO_TIME = {
    "1": "09:00",
    "2": "10:45",
    "3": "13:15",
    "4": "15:00",
    "5": "16:45",
    "6": "18:25"
}

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

    async def reset_period_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = [app_commands.Choice(name=p, value=p) for p in PERIOD_TO_TIME.keys() if current in p]
        if "all".startswith(current):
            choices.append(app_commands.Choice(name="all", value="all"))
        return choices

    @app_commands.command(name = "reset_period", description = "時限の開始時刻のリセットをします")
    @app_commands.describe(period = "リセットする時限（例：1）またはすべてリセットする場合は all")
    @app_commands.autocomplete(period = reset_period_autocomplete)
    async def reset_period(self, interaction: discord.Interaction, period: str):
        user_id = interaction.user.id
        data = load_user_data(user_id)

        if period == "all":
            data["period_overrides"] = {}
            save_user_data(user_id, data)
            await interaction.response.send_message("すべての時限設定をリセットしました。", ephemeral = True)
        else:
            overrides = data.get("period_overrides", {})
            if period in overrides:
                del overrides[period]
                data["period_overrides"] = overrides
                save_user_data(user_id, data)
                await interaction.response.send_message(f"{period}限の設定をリセットしました。", ephemeral = True)
            else:
                await interaction.response.send_message(f"{period}限の設定は登録されていません。", ephemeral = True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SettingCog(bot))