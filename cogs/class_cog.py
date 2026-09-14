import os
import re
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import matplotlib.pyplot as plt
import japanize_matplotlib
import logging
from utils import (
    load_user_data,
    save_user_data,
    get_user_data_mtime,
    send_dm,
    send_long_dm,
    get_current_term,
    get_effective_term,
    WEEKDAYS,
    WEEKDAY_MAP,
    PERIOD_TO_TIME,
    TERM_FIRST,
    TERM_SECOND,
    normalize_term_key,
)

logger = logging.getLogger(__name__)


class ClassCog(commands.GroupCog, name="class"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_cache = {}  # {user_id: {"mtime": 0, "data": {}}}
        super().__init__()

    def get_data(self, user_id):
        current_mtime = get_user_data_mtime(user_id)
        if user_id not in self.user_cache:
            logger.info(f"[DEBUG] 蛻晏屓繧ｭ繝｣繝・す繝･: user={user_id}")
        if (
            user_id not in self.user_cache
            or self.user_cache[user_id]["mtime"] < current_mtime
        ):
            logger.info(
                f"[DEBUG] 繧ｭ繝｣繝・す繝･繝ｪ繝ｭ繝ｼ繝・ user={user_id} (old={self.user_cache.get(user_id, {}).get('mtime')}, new={current_mtime})"
            )
            self.user_cache[user_id] = {
                "mtime": current_mtime,
                "data": load_user_data(user_id),
            }
        return self.user_cache[user_id]["data"]

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

    async def term_autocomplete(self, interaction: discord.Interaction, current: str):
        terms = [TERM_FIRST, TERM_SECOND]
        return [app_commands.Choice(name=t, value=t) for t in terms if current in t]

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
        if not choices and current.strip():
            choices.append(
                app_commands.Choice(
                    name=f"讀懃ｴ｢蛟呵｣懊↑縺暦ｼ域眠隕・ {current}・・, value=current
                )
            )
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

    @staticmethod
    def _tokenize_subject(text: str) -> list[str]:
        normalized = re.sub(r"[\t\r\n]+", " ", str(text or "").strip())
        if not normalized:
            return []
        chunks = re.split(r"[\s/繝ｻ,・後・)\[\]{}]+", normalized)
        return [x for x in chunks if x]

    @staticmethod
    def _wrap_tokens(tokens: list[str], max_chars: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for token in tokens:
            if len(token) > max_chars:
                if current:
                    lines.append(current)
                    current = ""
                for i in range(0, len(token), max_chars):
                    lines.append(token[i : i + max_chars])
                continue

            merged = token if not current else f"{current} {token}"
            if len(merged) <= max_chars:
                current = merged
            else:
                lines.append(current)
                current = token
        if current:
            lines.append(current)
        return lines

    @classmethod
    def _wrap_subject(
        cls, subject: str, max_chars: int = 10, max_lines: int = 4
    ) -> list[str]:
        tokens = cls._tokenize_subject(subject)
        if not tokens:
            return [""]
        lines = cls._wrap_tokens(tokens, max_chars)
        if len(lines) <= max_lines:
            return lines
        trimmed = lines[:max_lines]
        if len(trimmed[-1]) >= max_chars:
            trimmed[-1] = trimmed[-1][: max_chars - 1] + "窶ｦ"
        else:
            trimmed[-1] = trimmed[-1] + "窶ｦ"
        return trimmed

    @classmethod
    def _cell_label(cls, slot_classes: list[dict]) -> str:
        if not slot_classes:
            return "-"

        blocks: list[str] = []
        for c in slot_classes[:2]:
            subject = str(c.get("subject", "")).strip()
            room = str(c.get("room", "")).strip()
            subject_lines = cls._wrap_subject(subject, max_chars=10, max_lines=4)
            room_lines = cls._wrap_subject(room, max_chars=12, max_lines=2)
            block = "\n".join(subject_lines + room_lines)
            blocks.append(block)

        text = "\n\n".join(blocks)
        if len(slot_classes) > 2:
            text += "\n(+more)"
        return text

    @app_commands.command(
        name="table", description="謗域･ｭ荳隕ｧ繧呈凾髢灘牡陦ｨ蠖｢蠑上〒陦ｨ遉ｺ縺励∪縺・
    )
    @app_commands.describe(term="蟇ｾ雎｡蟄ｦ譛滂ｼ亥燕譛・蠕梧悄縲∵悴謖・ｮ壽凾縺ｯ迴ｾ蝨ｨ縺ｮ蟄ｦ譛滂ｼ・)
    @app_commands.autocomplete(term=term_autocomplete)
    async def class_table(
        self, interaction: discord.Interaction, term: str | None = None
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        logger.info(f"[Command] class table: user={user_id} term_arg={term}")
        data = self.get_data(user_id)
        selected_term = normalize_term_key(term) if term else get_effective_term(user_id)
        classes = data.get("classes_by_term", {}).get(selected_term, [])
        if not classes:
            logger.info(f"[Command] class table: user={user_id} - No classes found for {selected_term}")
            await interaction.followup.send(
                f"{selected_term}縺ｮ逋ｻ骭ｲ謗域･ｭ縺ｯ縺ゅｊ縺ｾ縺帙ｓ縲・, ephemeral=True
            )
            return

        table = {p: ["-" for _ in range(7)] for p in range(1, 7)}
        for c in classes:
            try:
                d = int(c["day"])
                p = int(c["period"])
                if p in table:
                    table[p][d] = c["subject"][:8]
            except Exception:
                continue

        header = "髯酢 譛・| 轣ｫ | 豌ｴ | 譛ｨ | 驥・| 蝨・| 譌･ \n"
        line = "--|---|---|---|---|---|---|---\n"
        body = ""
        for p, rows in table.items():
            body += f"{p} |" + "|".join(rows) + "\n"

        await interaction.followup.send(
            f"{selected_term} 縺ｮ譎る俣蜑ｲ\n```\n{header}{line}{body}```", ephemeral=True
        )

    @app_commands.command(
        name="add", description="謗域･ｭ繧堤匳骭ｲ縺励∪縺呻ｼ域屆譌･, 譎る剞, 遘醍岼, 謨吝ｮ､・・
    )
    @app_commands.describe(
        weekday="譖懈律繧帝∈謚・, period="譎る剞繧帝∈謚・, subject="遘醍岼蜷・, room="謨吝ｮ､"
    )
    @app_commands.autocomplete(
        weekday=weekday_autocomplete,
        period=period_autocomplete,
        subject=subject_autocomplete,
        room=room_autocomplete,
    )
    async def class_add(
        self,
        interaction: discord.Interaction,
        weekday: str,
        period: str,
        subject: str,
        room: str,
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        logger.info(f"[Command] class add: user={user_id} {weekday} {period}髯・{subject} ({room})")
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        data.setdefault("classes_by_term", {}).setdefault(term, [])
        data["classes_by_term"][term] = [
            c
            for c in data.get("classes_by_term", {}).get(term, [])
            if not (
                c.get("day") == WEEKDAY_MAP[weekday]
                and str(c.get("period")) == str(period)
            )
        ]
        data["classes_by_term"][term].append(
            {
                "day": WEEKDAY_MAP[weekday],
                "period": period,
                "time": PERIOD_TO_TIME.get(period),
                "subject": subject,
                "room": room,
            }
        )
        save_user_data(user_id, data)
        logger.info(f"[Data Change] class add: user={user_id} added class to {term}")
        await send_dm(
            interaction.user,
            f" 謗域･ｭ繧堤匳骭ｲ縺励∪縺励◆・嘴weekday} {period}髯・窶・{subject} ({room})",
        )
        await interaction.followup.send("謗域･ｭ繧奪M縺ｧ逋ｻ骭ｲ縺励∪縺励◆縲・, ephemeral=True)

    @app_commands.command(
        name="remove", description="謗域･ｭ繧貞炎髯､縺励∪縺呻ｼ域屆譌･・区凾髯舌〒謖・ｮ夲ｼ・
    )
    @app_commands.describe(weekday="譖懈律繧帝∈謚・, period="譎る剞繧帝∈謚・)
    @app_commands.autocomplete(weekday=weekday_autocomplete, period=period_autocomplete)
    async def class_remove(
        self, interaction: discord.Interaction, weekday: str, period: str
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        logger.info(f"[Command] class remove: user={user_id} {weekday} {period}髯・)
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        before = len(data.get("classes_by_term", {}).get(term, []))
        data.setdefault("classes_by_term", {}).setdefault(term, [])
        data["classes_by_term"][term] = [
            c
            for c in data.get("classes_by_term", {}).get(term, [])
            if not (
                c.get("day") == WEEKDAY_MAP[weekday]
                and str(c.get("period")) == str(period)
            )
        ]
        save_user_data(user_id, data)
        removed = before - len(data.get("classes_by_term", {}).get(term, []))
        logger.info(f"[Data Change] class remove: user={user_id} removed {removed} classes from {term}")
        await send_dm(
            interaction.user, f" {removed}莉ｶ繧貞炎髯､縺励∪縺励◆・嘴weekday} {period}髯・
        )
        await interaction.followup.send("蜑企勁邨先棡繧奪M縺ｧ騾∽ｿ｡縺励∪縺励◆縲・, ephemeral=True)

    @app_commands.command(
        name="list", description="逋ｻ骭ｲ謗域･ｭ・域屆譌･繝ｻ譎る剞鬆・ｼ峨ｒDM縺ｧ騾√ｊ縺ｾ縺・
    )
    @app_commands.describe(term="蟇ｾ雎｡蟄ｦ譛滂ｼ亥燕譛・蠕梧悄縲∵悴謖・ｮ壽凾縺ｯ迴ｾ蝨ｨ縺ｮ蟄ｦ譛滂ｼ・)
    @app_commands.autocomplete(term=term_autocomplete)
    async def class_list(
        self, interaction: discord.Interaction, term: str | None = None
    ):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.errors.InteractionResponded:
            pass
        except Exception as e:
            logger.error(f"Error in defer: {e}")
            return
        user_id = interaction.user.id
        logger.info(f"[Command] class list: user={user_id} term_arg={term}")
        data = self.get_data(user_id)
        selected_term = normalize_term_key(term) if term else get_effective_term(user_id)
        classes = data.get("classes_by_term", {}).get(selected_term, []) or []
        if not classes:
            logger.info(f"[Command] class list: user={user_id} - No classes found for {selected_term}")
            await send_dm(interaction.user, f"{selected_term}縺ｮ逋ｻ骭ｲ謗域･ｭ縺ｯ縺ゅｊ縺ｾ縺帙ｓ縲・)
            await interaction.followup.send(
                "DM繧帝∽ｿ｡縺励∪縺励◆・域肢讌ｭ縺ｪ縺暦ｼ峨・, ephemeral=True
            )
            return

        periods_sorted = sorted(
            [str(p) for p in PERIOD_TO_TIME.keys()],
            key=lambda x: int(x) if str(x).isdigit() else 999,
        )
        grid: dict[tuple[int, str], list[dict]] = {}
        for c in classes:
            try:
                day = int(c.get("day"))
            except Exception:
                continue
            period = str(c.get("period", "")).strip()
            if day < 0 or day >= len(WEEKDAYS) or period not in periods_sorted:
                continue
            grid.setdefault((day, period), []).append(c)

        cell_text = []
        for period in periods_sorted:
            row = []
            for day_idx in range(len(WEEKDAYS)):
                row.append(self._cell_label(grid.get((day_idx, period), [])))
            cell_text.append(row)

        fig_height = max(5.5, 1.5 + len(periods_sorted) * 1.45)
        fig, ax = plt.subplots(figsize=(15.5, fig_height))
        ax.axis("off")

        tbl = ax.table(
            cellText=cell_text,
            colLabels=WEEKDAYS,
            rowLabels=[f"{p}髯・ for p in periods_sorted],
            cellLoc="center",
            loc="center",
        )

        tbl.auto_set_font_size(False)
        tbl.set_fontsize(9)
        tbl.scale(1.0, 2.25)

        for (r, c), cell in tbl.get_celld().items():
            cell.set_edgecolor("#475569")
            cell.set_linewidth(1.0)
            text = cell.get_text()
            text.set_wrap(True)
            text.set_multialignment("center")
            if r == 0 or c == -1:
                cell.set_facecolor("#e2e8f0")
                text.set_fontweight("bold")
                text.set_color("#0f172a")
                text.set_fontsize(10)
            else:
                cell.set_facecolor("#f8fafc")
                text.set_color("#0f172a")
        img_path = f"class_list_{interaction.user.id}.png"
        fig.savefig(img_path, bbox_inches="tight", dpi=220)
        plt.close(fig)

        try:
            await interaction.user.send(
                content=f"{selected_term}縺ｮ逋ｻ骭ｲ謗域･ｭ荳隕ｧ縺ｧ縺吶・,
                file=discord.File(img_path),
            )
        finally:
            if os.path.exists(img_path):
                os.remove(img_path)
        await interaction.followup.send(
            "逋ｻ骭ｲ謗域･ｭ荳隕ｧ繧奪M縺ｧ騾∽ｿ｡縺励∪縺励◆縲・, ephemeral=True
        )

    @app_commands.command(
        name="setroom", description="迚ｹ螳壽律縺ｮ迚ｹ螳壽肢讌ｭ縺ｮ謨吝ｮ､繧貞､画峩縺励∪縺・
    )
    @app_commands.describe(
        date="YYYY-MM-DD", period="螟画峩縺吶ｋ譎る剞", new_room="譁ｰ縺励＞謨吝ｮ､蜷・
    )
    @app_commands.autocomplete(period=period_autocomplete, new_room=room_autocomplete)
    async def class_setroom(
        self, interaction: discord.Interaction, date: str, period: str, new_room: str
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except Exception:
            await interaction.followup.send(
                "譌･莉伜ｽ｢蠑上′辟｡蜉ｹ縺ｧ縺吶・YYY-MM-DD 縺ｧ謖・ｮ壹＠縺ｦ縺上□縺輔＞縲・, ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        target_weekday = target_date.weekday()
        found = False

        # Apply to regular timetable classes that are actually scheduled on this date.
        for cls in data.get("classes_by_term", {}).get(term, []):
            if str(cls.get("period")) != str(period):
                continue
            effective_day = cls.get("day")
            try:
                if date in (cls.get("overrides") or {}):
                    effective_day = (cls.get("overrides") or {}).get(date)
                effective_day = int(effective_day)
            except Exception:
                continue
            if effective_day != target_weekday:
                continue

            cls.setdefault("room_overrides", {})[date] = new_room
            found = True

        # Also apply to exam timetable classes that are active on this date.
        for sched in data.get("exam_schedules_by_term", {}).get(term, []) or []:
            start = str(sched.get("start", "")).strip()
            end = str(sched.get("end", "")).strip()
            if start and date < start:
                continue
            if end and date > end:
                continue

            for cls in sched.get("classes", []) or []:
                if str(cls.get("period")) != str(period):
                    continue
                try:
                    if int(cls.get("day")) != target_weekday:
                        continue
                except Exception:
                    continue
                cls.setdefault("room_overrides", {})[date] = new_room
                found = True

        if not found:
            await interaction.followup.send(
                "謖・ｮ壹・謗域･ｭ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ縺ｧ縺励◆縲・, ephemeral=True
            )
            return
        save_user_data(user_id, data)
        await send_dm(
            interaction.user,
            f" {date} 縺ｮ {period}髯・縺ｮ謨吝ｮ､繧・{new_room} 縺ｫ螟画峩縺励∪縺励◆縲・,
        )
        await interaction.followup.send("謨吝ｮ､螟画峩繧奪M縺ｫ騾∽ｿ｡縺励∪縺励◆縲・, ephemeral=True)

    @app_commands.command(
        name="setday", description="閾ｪ蛻・・迚ｹ螳壽律縺ｮ譖懈律繧貞､画峩・亥・謗域･ｭ縺ｫ驕ｩ逕ｨ・・
    )
    @app_commands.describe(date="YYYY-MM-DD", new_weekday="螟画峩蠕後・譖懈律")
    @app_commands.autocomplete(new_weekday=weekday_autocomplete)
    async def class_setday(
        self, interaction: discord.Interaction, date: str, new_weekday: str
    ):
        await interaction.response.defer(ephemeral=True)
        if new_weekday not in WEEKDAY_MAP:
            await interaction.followup.send("辟｡蜉ｹ縺ｪ譖懈律縺ｧ縺吶・, ephemeral=True)
            return
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except Exception:
            await interaction.followup.send(
                "譌･莉伜ｽ｢蠑上′辟｡蜉ｹ縺ｧ縺吶・YYY-MM-DD 縺ｧ謖・ｮ壹＠縺ｦ縺上□縺輔＞縲・, ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        for cls in data.get("classes_by_term", {}).get(term, []):
            cls.setdefault("overrides", {})[date] = WEEKDAY_MAP[new_weekday]
    @app_commands.command(
        name="status", description="迴ｾ蝨ｨ縺ｮ繧ｷ繧ｹ繝・Β蛻､螳壻ｸ翫・蟄ｦ譛溘ｒ陦ｨ遉ｺ縺励∪縺・
    )
    async def class_status(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        logger.info(f"[Command] class status: user={user_id}")
        term = get_effective_term(user_id)
        
        # 蛻､螳壹・繝ｭ繧ｻ繧ｹ縺ｮ蜿ｯ隕門喧
        data = self.get_data(user_id)
        settings = data.get("settings", {})
        month = datetime.now().month
        
        detail = ""
        term_settings = settings.get(term, {})
        if term_settings.get("start_date") and term_settings.get("end_date"):
            detail = f"・・eb險ｭ螳壹・譛滄俣蜀・ {term_settings['start_date']} 縲・{term_settings['end_date']}・・
        else:
            detail = f"・域怦縺ｫ繧医ｋ蛻､螳・ {month}譛茨ｼ・

        logger.info(f"[Command] class status: user={user_id} -> result={term}")
        await interaction.response.send_message(
            f"迴ｾ蝨ｨ縺ｯ **{term}** 縺ｧ縺吶・detail}", ephemeral=True
        )

    @app_commands.command(
        name="today", description="莉頑律縺ｮ謗域･ｭ繧ｹ繧ｱ繧ｸ繝･繝ｼ繝ｫ繧剃ｸ隕ｧ陦ｨ遉ｺ縺励∪縺・
    )
    async def class_today(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        logger.info(f"[Command] class today: user={user_id}")
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        day_idx = now.weekday()  # 0=Mon, 6=Sun
        day_name = WEEKDAYS[day_idx]

        # 逋ｻ骭ｲ謗域･ｭ縺ｮ蜿門ｾ・
        all_classes = data.get("classes_by_term", {}).get(term, []) or []
        today_classes = []

        for cls in all_classes:
            # 謖ｯ譖ｿ險ｭ螳壹・遒ｺ隱・
            effective_day = cls.get("day")
            overrides = cls.get("overrides") or {}
            if date_str in overrides:
                effective_day = overrides[date_str]
            
            if effective_day == day_idx:
                # 謨吝ｮ､縺ｮ蛟句挨螟画峩遒ｺ隱・
                room = cls.get("room", "譛ｪ險ｭ螳・)
                room_overrides = cls.get("room_overrides") or {}
                if date_str in room_overrides:
                    room = room_overrides[date_str]
                
                today_classes.append({
                    "period": cls.get("period"),
                    "subject": cls.get("subject"),
                    "room": room,
                    "time": cls.get("time", "荳肴・")
                })

        # 譎る剞鬆・↓繧ｽ繝ｼ繝・
        today_classes.sort(key=lambda x: int(x["period"]) if str(x["period"]).isdigit() else 99)

        if not today_classes:
            await interaction.followup.send(
                f"譛ｬ譌･・・date_str} {day_name}・峨・逋ｻ骭ｲ謗域･ｭ縺ｯ縺ゅｊ縺ｾ縺帙ｓ縲・, ephemeral=True
            )
            return

        msg = f"套 **譛ｬ譌･・・date_str} {day_name}・峨・謗域･ｭ荳隕ｧ**\n"
        msg += "--------------------------------------\n"
        for c in today_classes:
            msg += f"**{c['period']}髯・* ({c['time']}) : {c['subject']}\n"
            msg += f"   桃 謨吝ｮ､: {c['room']}\n"
        
        logger.info(f"[Command] class today: user={user_id} - found {len(today_classes)} classes")
        await interaction.followup.send(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ClassCog(bot))
