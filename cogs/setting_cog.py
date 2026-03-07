import discord
from discord import app_commands
from discord.ext import commands
import re
from utils import load_user_data, save_user_data, send_dm

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

    @app_commands.command(name = "show", description = "現在の時限設定をDMで表示します")
    async def show_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral = True)
        user_id = interaction.user.id
        data = load_user_data(user_id)

        period_overrides = data.get("period_overrides", {}) or {}
        exam_period_overrides = data.get("exam_period_overrides", {}) or {}

        lines = ["時限設定:"]

        lines.append("\n【通常時限の開始時刻カスタマイズ】")
        if period_overrides:
            for period, time in sorted(period_overrides.items()):
                lines.append(f"  {period}限: {time}")
        else:
            lines.append("  (設定なし)")

        lines.append("\n【試験時限の開始時刻カスタマイズ】")
        if exam_period_overrides:
            for period, time in sorted(exam_period_overrides.items()):
                lines.append(f"  {period}限: {time}")
        else:
            lines.append("  (設定なし)")

        await send_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send("設定内容をDMで送信しました。", ephemeral = True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SettingCog(bot))