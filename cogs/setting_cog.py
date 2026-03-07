import discord
from discord import app_commands
from discord.ext import commands
import re
from utils import load_user_data, save_user_data, PERIOD_TO_TIME, DEFAULT_NOTIFY

class SettingCog(commands.GroupCog, name="setting"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def period_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=p, value=p)
            for p in PERIOD_TO_TIME.keys()
            if current in p
        ]

    @app_commands.command(name="period_time", description="各時限の開始時刻を登録します（例：1限 → 09:00）")
    @app_commands.describe(period="設定する時限（例：1）", time="開始時刻（例：09:00）")
    @app_commands.autocomplete(period=period_autocomplete)
    async def set_period_time(self, interaction: discord.Interaction, period: str, time: str):
        if not re.match(r"^(2[0-3]|[01]?\d):[0-5]\d$", time):
            await interaction.response.send_message(
                "時刻は「HH:MM」の形式で入力してください（例：09:00）。時刻は0〜23時：0〜59分で入力してください。",
                ephemeral=True
            )
            return

        user_id = interaction.user.id
        data = load_user_data(user_id)
        data.setdefault("period_overrides", {})[period] = time
        save_user_data(user_id, data)

        await interaction.response.send_message(
            f"{period}限の開始時刻を {time} に設定しました。",
            ephemeral=True
        )

    @app_commands.command(name="show", description="現在の時限設定を表示します（DM）")
    async def show_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)

        period_overrides = data.get("period_overrides", {}) or {}
        notify_settings = data.get("notify_settings", {}) or {}
        normal_cfg = notify_settings.get("normal") or DEFAULT_NOTIFY["normal"]
        exam_cfg = notify_settings.get("exam") or DEFAULT_NOTIFY["exam"]

        lines = ["現在の設定:"]
        lines.append("\n【時限の開始時刻】")
        for p in sorted(PERIOD_TO_TIME.keys(), key=int):
            override = period_overrides.get(p)
            default = PERIOD_TO_TIME[p]
            if override:
                lines.append(f"  {p}限: {override}（カスタム設定）")
            else:
                lines.append(f"  {p}限: {default}（デフォルト）")

        lines.append("\n【通知タイミング】")
        lines.append(f"  通常授業: {normal_cfg.get('first')}分前 / {normal_cfg.get('second')}分前")
        lines.append(f"  試験期間: {exam_cfg.get('first')}分前 / {exam_cfg.get('second')}分前")

        try:
            dm = await interaction.user.create_dm()
            await dm.send("\n".join(lines))
        except Exception as e:
            await interaction.followup.send(f"DM送信に失敗しました: {e}", ephemeral=True)
            return

        await interaction.followup.send("設定内容をDMで送信しました。", ephemeral=True)

    @app_commands.command(name="reset_period", description="時限の開始時刻をデフォルトに戻します")
    @app_commands.describe(period="リセットする時限（例：1）、all で全てリセット")
    @app_commands.autocomplete(period=period_autocomplete)
    async def reset_period_time(self, interaction: discord.Interaction, period: str):
        user_id = interaction.user.id
        data = load_user_data(user_id)
        overrides = data.get("period_overrides", {}) or {}

        if period.lower() == "all":
            data["period_overrides"] = {}
            save_user_data(user_id, data)
            await interaction.response.send_message(
                "全時限の開始時刻をデフォルトにリセットしました。",
                ephemeral=True
            )
            return

        if period not in overrides:
            await interaction.response.send_message(
                f"{period}限にはカスタム設定がありません。",
                ephemeral=True
            )
            return

        del overrides[period]
        data["period_overrides"] = overrides
        save_user_data(user_id, data)
        await interaction.response.send_message(
            f"{period}限の開始時刻をデフォルト（{PERIOD_TO_TIME.get(period, '不明')}）にリセットしました。",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(SettingCog(bot))