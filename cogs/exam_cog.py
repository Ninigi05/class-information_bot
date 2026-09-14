import discord
from discord import app_commands
from discord.ext import commands
import re
from utils import (
    load_user_data,
    save_user_data,
    get_user_data_mtime,
    send_dm,
    send_long_dm,
    get_effective_term,
    WEEKDAYS,
    WEEKDAY_MAP,
    PERIOD_TO_TIME,
)
from datetime import datetime


class ExamCog(commands.GroupCog, name="exam"):
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

    async def exam_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        schedules = data.get("exam_schedules_by_term", {}).get(term, []) or []
        choices = []
        for s in schedules:
            name = s.get("name", "")
            if current in name:
                choices.append(app_commands.Choice(name=name, value=name))
                if len(choices) >= 25:
                    break
        return choices

    async def weekday_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        return [app_commands.Choice(name=w, value=w) for w in WEEKDAYS if current in w]

    async def period_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=p, value=p)
            for p in PERIOD_TO_TIME.keys()
            if current in p
        ]

    async def subject_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        subjects = []
        for c in data.get("classes_by_term", {}).get(term, []) or []:
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

    async def room_autocomplete(self, interaction: discord.Interaction, current: str):
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        rooms = []
        for c in data.get("classes_by_term", {}).get(term, []) or []:
            r = str(c.get("room", "")).strip()
            if r and current in r:
                rooms.append(r)
        for m in data.get("makeup_classes", []) or []:
            r = str(m.get("room", "")).strip()
            if r and current in r:
                rooms.append(r)
        seen = set()
        choices = []
        for r in rooms:
            if r not in seen:
                seen.add(r)
                choices.append(app_commands.Choice(name=r, value=r))
                if len(choices) >= 25:
                    break
        return choices

    @app_commands.command(
        name="set_time", description="隧ｦ鬨薙・蜷・凾髯宣幕蟋区凾髢薙ｒ逋ｻ骭ｲ縺励∪縺・
    )
    @app_commands.describe(
        period="險ｭ螳壹☆繧区凾髯撰ｼ井ｾ具ｼ夲ｼ托ｼ・, time="髢句ｧ区凾蛻ｻ・井ｾ具ｼ・9:00・・
    )
    async def set_exam_period_time(
        self, interaction: discord.Interaction, period: str, time: str
    ):
        if not re.match(r"^(2[0-3]|[01]?\d):[0-5]\d$", time):
            await interaction.response.send_message(
                "譎ょ綾縺ｯ縲粂H:MM縲阪・蠖｢蠑上〒蜈･蜉帙＠縺ｦ縺上□縺輔＞・井ｾ具ｼ・9:00・峨よ凾蛻ｻ縺ｯ0縲・3譎ゑｼ・縲・9蛻・〒蜈･蜉帙＠縺ｦ荳九＆縺・・,
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        data = self.get_data(user_id)

        if "exam_period_overrides" not in data:
            data["exam_period_overrides"] = {}

        data["exam_period_overrides"][period] = time
        save_user_data(user_id, data)

        await interaction.response.send_message(
            f"隧ｦ鬨捺凾縺ｮ{period}髯舌・髢句ｧ区凾蛻ｻ繧畜time}縺ｫ險ｭ螳壹＠縺ｾ縺励◆縲・, ephemeral=True
        )

    @app_commands.command(
        name="create", description="隧ｦ鬨鍋畑譎る俣蜑ｲ繧剃ｽ懈・縺励∪縺呻ｼ亥錐蜑阪・譛滄俣・・
    )
    @app_commands.describe(
        name="譎る俣蜑ｲ蜷・, start="髢句ｧ区律 (YYYY-MM-DD)", end="邨ゆｺ・律 (YYYY-MM-DD)"
    )
    async def exam_create(
        self, interaction: discord.Interaction, name: str, start: str, end: str
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            sd = datetime.strptime(start, "%Y-%m-%d").date()
            ed = datetime.strptime(end, "%Y-%m-%d").date()
        except Exception:
            await interaction.followup.send(
                "譌･莉伜ｽ｢蠑上′辟｡蜉ｹ縺ｧ縺吶・YYY-MM-DD 縺ｧ謖・ｮ壹＠縺ｦ縺上□縺輔＞縲・, ephemeral=True
            )
            return
        if sd > ed:
            await interaction.followup.send(
                "髢句ｧ区律縺ｯ邨ゆｺ・律繧医ｊ蜑阪↓縺励※縺上□縺輔＞縲・, ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        data.setdefault("exam_schedules_by_term", {}).setdefault(term, [])
        for s in data["exam_schedules_by_term"][term]:
            if s.get("name") == name:
                await interaction.followup.send(
                    "蜷悟錐縺ｮ隧ｦ鬨捺凾髢灘牡縺梧里縺ｫ蟄伜惠縺励∪縺吶・, ephemeral=True
                )
                return
        data["exam_schedules_by_term"][term].append(
            {"name": name, "start": start, "end": end, "classes": []}
        )
        save_user_data(user_id, data)
        await send_dm(
            interaction.user, f" 隧ｦ鬨捺凾髢灘牡縲鶏name}縲阪ｒ菴懈・縺励∪縺励◆: {start} ・・{end}"
        )
        await interaction.followup.send(
            "隧ｦ鬨捺凾髢灘牡繧奪M縺ｧ菴懈・縺励∪縺励◆縲・, ephemeral=True
        )

    @app_commands.command(name="delete", description="謖・ｮ壹＠縺溯ｩｦ鬨捺凾髢灘牡繧貞炎髯､縺励∪縺・)
    @app_commands.describe(name="蜑企勁縺吶ｋ譎る俣蜑ｲ蜷・)
    @app_commands.autocomplete(name=exam_name_autocomplete)
    async def exam_delete(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        before = len(data.get("exam_schedules_by_term", {}).get(term, []) or [])
        data.setdefault("exam_schedules_by_term", {}).setdefault(term, [])
        data["exam_schedules_by_term"][term] = [
            s
            for s in data.get("exam_schedules_by_term", {}).get(term, [])
            if s.get("name") != name
        ]
        save_user_data(user_id, data)
        removed = before - len(
            data.get("exam_schedules_by_term", {}).get(term, []) or []
        )
        await send_dm(
            interaction.user, f"卵・・隧ｦ鬨捺凾髢灘牡縲鶏name}縲阪ｒ蜑企勁縺励∪縺励◆・・removed}莉ｶ・峨・
        )
        await interaction.followup.send("蜑企勁邨先棡繧奪M縺ｧ騾∽ｿ｡縺励∪縺励◆縲・, ephemeral=True)

    @app_commands.command(
        name="list", description="逋ｻ骭ｲ貂医∩隧ｦ鬨捺凾髢灘牡縺ｮ荳隕ｧ繧奪M縺ｧ陦ｨ遉ｺ縺励∪縺・
    )
    async def exam_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        schedules = data.get("exam_schedules_by_term", {}).get(term, []) or []
        if not schedules:
            await send_dm(interaction.user, "隧ｦ鬨捺凾髢灘牡縺ｯ逋ｻ骭ｲ縺輔ｌ縺ｦ縺・∪縺帙ｓ縲・)
            await interaction.followup.send(
                "DM繧帝∽ｿ｡縺励∪縺励◆・郁ｩｦ鬨捺凾髢灘牡縺ｪ縺暦ｼ峨・, ephemeral=True
            )
            return
        lines = [" 逋ｻ骭ｲ貂医∩隧ｦ鬨捺凾髢灘牡:"]
        for s in sorted(schedules, key=lambda x: x.get("start", "")):
            lines.append(
                f"- {s.get('name')} : {s.get('start')} ・・{s.get('end')} ({len(s.get('classes', []))}莉ｶ)"
            )
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send(
            "隧ｦ鬨捺凾髢灘牡荳隕ｧ繧奪M縺ｧ騾∽ｿ｡縺励∪縺励◆縲・, ephemeral=True
        )

    @app_commands.command(
        name="show", description="謖・ｮ壹＠縺溯ｩｦ鬨捺凾髢灘牡縺ｮ荳ｭ霄ｫ繧定｡ｨ遉ｺ縺励∪縺・
    )
    @app_commands.describe(name="陦ｨ遉ｺ縺吶ｋ譎る俣蜑ｲ蜷・)
    @app_commands.autocomplete(name=exam_name_autocomplete)
    async def exam_show(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        schedules = data.get("exam_schedules_by_term", {}).get(term, []) or []
        target = next((s for s in schedules if s.get("name") == name), None)
        if not target:
            await interaction.followup.send(
                "隧ｲ蠖薙・譎る俣蜑ｲ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ縺ｧ縺励◆縲・, ephemeral=True
            )
            return
        classes = target.get("classes", []) or []
        if not classes:
            await send_dm(
                interaction.user, f"隧ｦ鬨捺凾髢灘牡縲鶏name}縲阪↓縺ｯ謗域･ｭ縺檎匳骭ｲ縺輔ｌ縺ｦ縺・∪縺帙ｓ縲・
            )
            await interaction.followup.send(
                "DM繧帝∽ｿ｡縺励∪縺励◆・域肢讌ｭ縺ｪ縺暦ｼ峨・, ephemeral=True
            )
            return
        lines = [f" 隧ｦ鬨捺凾髢灘牡縲鶏name}縲阪・謗域･ｭ:"]
        classes_sorted = sorted(
            classes,
            key=lambda x: (
                x.get("day", 0),
                int(x.get("period", 0) if str(x.get("period")).isdigit() else 999),
            ),
        )
        for c in classes_sorted:
            wd = WEEKDAYS[int(c.get("day"))] if c.get("day") is not None else "荳肴・譖懈律"
            period_display = c.get("period") or c.get("time") or "?"
            lines.append(
                f"{wd} {period_display}髯・{c.get('subject', '')} ({c.get('room', '譛ｪ險ｭ螳・)})"
            )
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send(
            "隧ｦ鬨捺凾髢灘牡繧奪M縺ｧ騾∽ｿ｡縺励∪縺励◆縲・, ephemeral=True
        )

    @app_commands.command(name="addclass", description="隧ｦ鬨捺凾髢灘牡縺ｫ謗域･ｭ繧定ｿｽ蜉縺励∪縺・)
    @app_commands.describe(
        name="譎る俣蜑ｲ蜷・,
        weekday="譖懈律",
        period="譎る剞",
        subject="遘醍岼蜷・,
        room="謨吝ｮ､",
        time="・井ｻｻ諢擾ｼ蛾幕蟋区凾蛻ｻ HH:MM",
    )
    @app_commands.autocomplete(
        name=exam_name_autocomplete,
        weekday=weekday_autocomplete,
        period=period_autocomplete,
        subject=subject_autocomplete,
        room=room_autocomplete,
    )
    async def exam_addclass(
        self,
        interaction: discord.Interaction,
        name: str,
        weekday: str,
        period: str,
        subject: str,
        room: str,
        time: str = None,
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        schedules = data.get("exam_schedules_by_term", {}).get(term, []) or []
        target = next((s for s in schedules if s.get("name") == name), None)
        if not target:
            await interaction.followup.send(
                "隧ｲ蠖薙・譎る俣蜑ｲ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ縺ｧ縺励◆縲・, ephemeral=True
            )
            return
        day_idx = WEEKDAY_MAP.get(weekday)
        time_val = time or PERIOD_TO_TIME.get(period)
        entry = {
            "day": day_idx,
            "period": period,
            "time": time_val,
            "subject": subject,
            "room": room,
        }
        target.setdefault("classes", []).append(entry)
        save_user_data(user_id, data)
        await send_dm(
            interaction.user,
            f" 隧ｦ鬨捺凾髢灘牡縲鶏name}縲阪↓謗域･ｭ繧定ｿｽ蜉縺励∪縺励◆: {weekday} {period}髯・{subject} ({room})",
        )
        await interaction.followup.send(
            "隧ｦ鬨捺凾髢灘牡縺ｫ謗域･ｭ繧定ｿｽ蜉縺励∪縺励◆・・M騾∽ｻ假ｼ峨・, ephemeral=True
        )

    @app_commands.command(
        name="removeclass",
        description="隧ｦ鬨捺凾髢灘牡縺九ｉ謗域･ｭ繧貞炎髯､縺励∪縺呻ｼ域屆譌･・区凾髯舌〒謖・ｮ夲ｼ・,
    )
    @app_commands.describe(name="譎る俣蜑ｲ蜷・, weekday="譖懈律", period="譎る剞")
    @app_commands.autocomplete(
        name=exam_name_autocomplete,
        weekday=weekday_autocomplete,
        period=period_autocomplete,
    )
    async def exam_removeclass(
        self, interaction: discord.Interaction, name: str, weekday: str, period: str
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        schedules = data.get("exam_schedules_by_term", {}).get(term, []) or []
        target = next((s for s in schedules if s.get("name") == name), None)
        if not target:
            await interaction.followup.send(
                "隧ｲ蠖薙・譎る俣蜑ｲ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ縺ｧ縺励◆縲・, ephemeral=True
            )
            return
        day_idx = WEEKDAY_MAP.get(weekday)
        before = len(target.get("classes", []))
        target["classes"] = [
            c
            for c in target.get("classes", [])
            if not (c.get("day") == day_idx and str(c.get("period")) == str(period))
        ]
        save_user_data(user_id, data)
        deleted = before - len(target.get("classes", []))
        await send_dm(
            interaction.user,
            f" 隧ｦ鬨捺凾髢灘牡縲鶏name}縲阪°繧・{weekday} {period}髯・繧貞炎髯､縺励∪縺励◆・・deleted}莉ｶ・峨・,
        )
        await interaction.followup.send("蜑企勁邨先棡繧奪M縺ｧ騾∽ｿ｡縺励∪縺励◆縲・, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ExamCog(bot))
