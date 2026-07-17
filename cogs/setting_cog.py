import discord
from discord import app_commands
from discord.ext import commands
import re
from datetime import datetime
from utils import (
    load_user_data,
    save_user_data,
    get_user_data_mtime,
    PERIOD_TO_TIME,
    DEFAULT_NOTIFY,
    TERM_FIRST,
    TERM_SECOND,
    normalize_term_key,
)


class SettingCog(commands.GroupCog, name="setting"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_cache = {}  # {user_id: {"mtime": 0, "data": {}}}
        super().__init__()

    def get_data(self, user_id):
        current_mtime = get_user_data_mtime(user_id)
        if (
            user_id not in self.user_cache
            or self.user_cache[user_id]["mtime"] < current_mtime
        ):
            self.user_cache[user_id] = {
                "mtime": current_mtime,
                "data": load_user_data(user_id),
            }
        return self.user_cache[user_id]["data"]

    async def period_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=p, value=p)
            for p in PERIOD_TO_TIME.keys()
            if current in p
        ]

    async def reset_period_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        options = list(PERIOD_TO_TIME.keys()) + ["all"]
        return [app_commands.Choice(name=p, value=p) for p in options if current in p]

    async def term_autocomplete(self, interaction: discord.Interaction, current: str):
        terms = [TERM_FIRST, TERM_SECOND]
        return [app_commands.Choice(name=t, value=t) for t in terms if current in t]

    @app_commands.command(
        name="period_time",
        description="各時限の開始時刻を登録します（例：1限 → 09:00）",
    )
    @app_commands.describe(period="設定する時限（例：1）", time="開始時刻（例：09:00）")
    @app_commands.autocomplete(period=period_autocomplete)
    async def set_period_time(
        self, interaction: discord.Interaction, period: str, time: str
    ):
        if not re.match(r"^(2[0-3]|[01]?\d):[0-5]\d$", time):
            await interaction.response.send_message(
                "時刻は「HH:MM」の形式で入力してください（例：09:00）。時刻は0〜23時：0〜59分で入力してください。",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        data = self.get_data(user_id)
        data.setdefault("period_overrides", {})[period] = time
        save_user_data(user_id, data)

        await interaction.response.send_message(
            f"{period}限の開始時刻を {time} に設定しました。", ephemeral=True
        )

    @app_commands.command(name="show", description="現在の時限設定を表示します（DM）")
    async def show_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = self.get_data(user_id)

        period_overrides = data.get("period_overrides", {}) or {}
        notify_settings = data.get("notify_settings", {}) or {}
        normal_cfg = notify_settings.get("normal") or DEFAULT_NOTIFY["normal"]
        exam_cfg = notify_settings.get("exam") or DEFAULT_NOTIFY["exam"]
        term_starts = data.get("term_start_dates", {}) or {}
        class_counts = data.get("class_count_targets", {}) or {}

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
        lines.append(
            f"  通常授業: {normal_cfg.get('first')}分前 / {normal_cfg.get('second')}分前"
        )
        lines.append(
            f"  試験期間: {exam_cfg.get('first')}分前 / {exam_cfg.get('second')}分前"
        )

        lines.append("\n【学期開始日と授業回数】")
        for term in [TERM_FIRST, TERM_SECOND]:
            start_date = term_starts.get(term, "未設定")
            count = class_counts.get(term, "未設定")
            lines.append(f"  {term}: 開始日 {start_date} / 授業回数 {count}回")

        try:
            dm = await interaction.user.create_dm()
            await dm.send("\n".join(lines))
        except Exception as e:
            await interaction.followup.send(
                f"DM送信に失敗しました: {e}", ephemeral=True
            )
            return

        await interaction.followup.send("設定内容をDMで送信しました。", ephemeral=True)

    @app_commands.command(
        name="reset_period", description="時限の開始時刻をデフォルトに戻します"
    )
    @app_commands.describe(period="リセットする時限（例：1）、all で全てリセット")
    @app_commands.autocomplete(period=reset_period_autocomplete)
    async def reset_period_time(self, interaction: discord.Interaction, period: str):
        user_id = interaction.user.id
        data = self.get_data(user_id)
        overrides = data.get("period_overrides", {}) or {}

        if period.lower() == "all":
            data["period_overrides"] = {}
            save_user_data(user_id, data)
            await interaction.response.send_message(
                "全時限の開始時刻をデフォルトにリセットしました。", ephemeral=True
            )
            return

        if period not in overrides:
            await interaction.response.send_message(
                f"{period}限にはカスタム設定がありません。", ephemeral=True
            )
            return

        del overrides[period]
        data["period_overrides"] = overrides
        save_user_data(user_id, data)
        await interaction.response.send_message(
            f"{period}限の開始時刻をデフォルト（{PERIOD_TO_TIME.get(period, '不明')}）にリセットしました。",
            ephemeral=True,
        )

    @app_commands.command(
        name="term_start", description="学期の開始日を設定します（前期または後期）"
    )
    @app_commands.describe(
        term="学期（前期 または 後期）", date="開始日（YYYY-MM-DD 形式）"
    )
    @app_commands.autocomplete(term=term_autocomplete)
    async def set_term_start(
        self, interaction: discord.Interaction, term: str, date: str
    ):
        term = normalize_term_key(term)
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                "日付は「YYYY-MM-DD」の形式で入力してください（例：2026-04-01）。",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        data = self.get_data(user_id)
        data.setdefault("term_start_dates", {})[term] = date
        save_user_data(user_id, data)

        await interaction.response.send_message(
            f"{term}の開始日を {date} に設定しました。", ephemeral=True
        )

    @app_commands.command(
        name="class_count", description="学期の授業回数を設定します（通常時間割）"
    )
    @app_commands.describe(
        term="学期（前期 または 後期）", count="授業回数（1 以上の整数）"
    )
    @app_commands.autocomplete(term=term_autocomplete)
    async def set_class_count(
        self, interaction: discord.Interaction, term: str, count: int
    ):
        term = normalize_term_key(term)
        if count < 1:
            await interaction.response.send_message(
                "授業回数は1以上の整数で入力してください。", ephemeral=True
            )
            return

        user_id = interaction.user.id
        data = self.get_data(user_id)
        data.setdefault("class_count_targets", {})[term] = count

        # Reset attendance counts when updating target
        attendance = data.get("class_attendance_count", {}) or {}
        attendance[term] = {}
        data["class_attendance_count"] = attendance

        save_user_data(user_id, data)

        await interaction.response.send_message(
            f"{term}の授業回数を {count} 回に設定しました。出席回数はリセットされました。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(SettingCog(bot))
