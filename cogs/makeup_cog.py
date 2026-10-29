import logging
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
from utils import (
    load_user_data,
    save_user_data,
    send_dm,
    get_effective_term,
    get_term_for_date,
    get_regular_classes_on_date,
    add_makeup_class,
    remove_makeup_class,
    add_cancelled_class,
    remove_cancelled_class,
    WEEKDAYS,
    PERIOD_TO_TIME,
)

logger = logging.getLogger(__name__)
WEEKDAY_SHORT = ["月", "火", "水", "木", "金", "土", "日"]


def _valid_date(value) -> str | None:
    try:
        datetime.strptime(str(value or ""), "%Y-%m-%d")
        return str(value)
    except ValueError:
        return None


def _format_date(date_str: str) -> str:
    try:
        wd = datetime.strptime(date_str, "%Y-%m-%d").weekday()
        return f"{date_str}({WEEKDAY_SHORT[wd]})"
    except ValueError:
        return date_str


async def date_autocomplete(interaction: discord.Interaction, current: str):
    today = datetime.now().date()
    choices = []
    for i in range(60):
        d = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        if current in d:
            choices.append(app_commands.Choice(name=_format_date(d), value=d))
            if len(choices) >= 25:
                break
    return choices


async def period_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=f"{p}限 ({t})", value=p)
        for p, t in PERIOD_TO_TIME.items()
        if current in p
    ]


class MakeupCog(commands.GroupCog, name="makeup"):
    """補講: 指定日・時限に既存の授業を追加する"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def source_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        user_id = interaction.user.id
        data = load_user_data(user_id)
        date_str = _valid_date(getattr(interaction.namespace, "date", None))
        term = (
            get_term_for_date(data, date_str)
            if date_str
            else get_effective_term(user_id)
        )
        classes = sorted(
            data.get("classes_by_term", {}).get(term, []) or [],
            key=lambda c: (int(c.get("day", 0)), str(c.get("period", ""))),
        )
        choices = []
        for c in classes:
            try:
                label = f"{WEEKDAYS[int(c.get('day'))]} {c.get('period')}限 {c.get('subject')}"
            except (TypeError, ValueError, IndexError):
                continue
            if current in label:
                choices.append(
                    app_commands.Choice(
                        name=label[:100], value=f"{c.get('day')}|{c.get('period')}"
                    )
                )
                if len(choices) >= 25:
                    break
        return choices

    async def registered_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        data = load_user_data(interaction.user.id)
        choices = []
        for m in data.get("makeup_classes", []) or []:
            label = f"{_format_date(m.get('date', ''))} {m.get('period')}限 {m.get('subject')}"
            if current in label:
                choices.append(
                    app_commands.Choice(
                        name=label[:100],
                        value=f"{m.get('date')}|{m.get('period')}|{m.get('day')}|{m.get('source_period')}",
                    )
                )
                if len(choices) >= 25:
                    break
        return choices

    @app_commands.command(
        name="add", description="補講を追加します（指定日・時限に既存の授業を追加）"
    )
    @app_commands.describe(
        date="補講日（YYYY-MM-DD）",
        period="補講を行う時限",
        source="補講対象の授業（登録済みの授業から選択）",
        room="教室（未指定時は元の授業の教室）",
    )
    @app_commands.autocomplete(
        date=date_autocomplete,
        period=period_autocomplete,
        source=source_autocomplete,
    )
    async def makeup_add(
        self,
        interaction: discord.Interaction,
        date: str,
        period: str,
        source: str,
        room: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        logger.info(
            f"[Command] makeup add: user={user_id} {date} {period}限 source={source}"
        )
        try:
            source_day_raw, source_period = source.split("|", 1)
            source_day = int(source_day_raw)
        except ValueError:
            await interaction.followup.send(
                "授業は候補から選択してください", ephemeral=True
            )
            return

        data = load_user_data(user_id)
        try:
            record = add_makeup_class(
                data, date, period, source_day, source_period, room=room
            )
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        save_user_data(user_id, data)
        logger.info(f"[Data Change] makeup add: user={user_id} {record}")
        await send_dm(
            interaction.user,
            f"補講を登録しました：{_format_date(record['date'])} {record['period']}限 "
            f"{record['subject']} ({record['room']})\n"
            f"※元の授業（{WEEKDAYS[record['day']]} {record['source_period']}限）の授業回数として数えます",
        )
        await interaction.followup.send("補講を登録しました（DMを確認してください）", ephemeral=True)

    @app_commands.command(name="remove", description="登録済みの補講を削除します")
    @app_commands.describe(target="削除する補講")
    @app_commands.autocomplete(target=registered_autocomplete)
    async def makeup_remove(self, interaction: discord.Interaction, target: str):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        logger.info(f"[Command] makeup remove: user={user_id} target={target}")
        date_str, period, source_day, source_period = (target.split("|") + [None] * 4)[:4]
        data = load_user_data(user_id)
        try:
            removed = remove_makeup_class(
                data, date_str, period, source_day, source_period
            )
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        if removed is None:
            await interaction.followup.send(
                "該当する補講が見つかりませんでした", ephemeral=True
            )
            return
        save_user_data(user_id, data)
        await send_dm(
            interaction.user,
            f"補講を削除しました：{_format_date(removed['date'])} {removed['period']}限 {removed.get('subject')}",
        )
        await interaction.followup.send("補講を削除しました", ephemeral=True)

    @app_commands.command(name="list", description="登録済みの補講一覧を表示します")
    async def makeup_list(self, interaction: discord.Interaction):
        data = load_user_data(interaction.user.id)
        makeups = data.get("makeup_classes", []) or []
        if not makeups:
            await interaction.response.send_message(
                "登録されている補講はありません", ephemeral=True
            )
            return
        lines = ["📘 **補講一覧**"]
        for m in makeups:
            lines.append(
                f"・{_format_date(m.get('date', ''))} {m.get('period')}限 {m.get('subject')} ({m.get('room', '未設定')})"
            )
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)


class CancelCog(commands.GroupCog, name="cancel"):
    """休講: 指定日の特定の授業を通知せず、授業回数にも数えない"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def class_on_date_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        date_str = _valid_date(getattr(interaction.namespace, "date", None))
        if not date_str:
            return await period_autocomplete(interaction, current)
        data = load_user_data(interaction.user.id)
        term = get_term_for_date(data, date_str)
        classes = sorted(
            get_regular_classes_on_date(data, term, date_str),
            key=lambda c: str(c.get("period", "")),
        )
        choices = []
        for c in classes:
            label = f"{c.get('period')}限 {c.get('subject')}"
            if current in label:
                choices.append(
                    app_commands.Choice(name=label[:100], value=str(c.get("period")))
                )
        return choices[:25]

    async def registered_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        data = load_user_data(interaction.user.id)
        choices = []
        for c in data.get("cancelled_classes", []) or []:
            label = f"{_format_date(c.get('date', ''))} {c.get('period')}限 {c.get('subject')}"
            if current in label:
                choices.append(
                    app_commands.Choice(
                        name=label[:100], value=f"{c.get('date')}|{c.get('period')}"
                    )
                )
                if len(choices) >= 25:
                    break
        return choices

    @app_commands.command(
        name="add", description="休講を登録します（その日の授業を通知・カウントしない）"
    )
    @app_commands.describe(date="休講日（YYYY-MM-DD）", period="休講になる授業")
    @app_commands.autocomplete(
        date=date_autocomplete, period=class_on_date_autocomplete
    )
    async def cancel_add(
        self, interaction: discord.Interaction, date: str, period: str
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        logger.info(f"[Command] cancel add: user={user_id} {date} {period}限")
        data = load_user_data(user_id)
        try:
            record = add_cancelled_class(data, date, period)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        save_user_data(user_id, data)
        logger.info(f"[Data Change] cancel add: user={user_id} {record}")
        await send_dm(
            interaction.user,
            f"休講を登録しました：{_format_date(record['date'])} {record['period']}限 {record['subject']}\n"
            "※この授業は通知されず、授業回数にも数えません",
        )
        await interaction.followup.send("休講を登録しました（DMを確認してください）", ephemeral=True)

    @app_commands.command(name="remove", description="登録済みの休講を取り消します")
    @app_commands.describe(target="取り消す休講")
    @app_commands.autocomplete(target=registered_autocomplete)
    async def cancel_remove(self, interaction: discord.Interaction, target: str):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        logger.info(f"[Command] cancel remove: user={user_id} target={target}")
        date_str, _, period = target.partition("|")
        data = load_user_data(user_id)
        removed = remove_cancelled_class(data, date_str, period)
        if removed is None:
            await interaction.followup.send(
                "該当する休講が見つかりませんでした", ephemeral=True
            )
            return
        save_user_data(user_id, data)
        await send_dm(
            interaction.user,
            f"休講を取り消しました：{_format_date(removed['date'])} {removed['period']}限 {removed.get('subject')}",
        )
        await interaction.followup.send("休講を取り消しました", ephemeral=True)

    @app_commands.command(name="list", description="登録済みの休講一覧を表示します")
    async def cancel_list(self, interaction: discord.Interaction):
        data = load_user_data(interaction.user.id)
        cancels = data.get("cancelled_classes", []) or []
        if not cancels:
            await interaction.response.send_message(
                "登録されている休講はありません", ephemeral=True
            )
            return
        lines = ["🚫 **休講一覧**"]
        for c in cancels:
            lines.append(
                f"・{_format_date(c.get('date', ''))} {c.get('period')}限 {c.get('subject')}"
            )
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MakeupCog(bot))
    await bot.add_cog(CancelCog(bot))
