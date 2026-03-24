import os
import re
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import matplotlib.pyplot as plt
import japanize_matplotlib
from utils import (
    load_user_data,
    save_user_data,
    send_dm,
    send_long_dm,
    get_current_term,
    WEEKDAYS,
    WEEKDAY_MAP,
    PERIOD_TO_TIME,
)


class ClassCog(commands.GroupCog, name="class"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

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
        data = load_user_data(user_id)
        term = get_current_term()
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
                    name=f"検索候補なし（新規: {current}）", value=current
                )
            )
        return choices

    async def room_autocomplete(self, interaction: discord.Interaction, current: str):
        user_id = interaction.user.id
        data = load_user_data(user_id)
        term = get_current_term()
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
        chunks = re.split(r"[\s/・,，、()\[\]{}]+", normalized)
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
            trimmed[-1] = trimmed[-1][: max_chars - 1] + "…"
        else:
            trimmed[-1] = trimmed[-1] + "…"
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
        name="table", description="授業一覧を時間割表形式で表示します"
    )
    async def class_table(self, interaction: discord.Interaction):
        data = load_user_data(interaction.user.id)
        term = get_current_term()
        classes = data.get("classes_by_term", {}).get(term, [])
        if not classes:
            await interaction.response.send_message(
                "登録されている授業はありません。", ephemeral=True
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

        header = "限| 月 | 火 | 水 | 木 | 金 | 土 | 日 \n"
        line = "--|---|---|---|---|---|---|---\n"
        body = ""
        for p, rows in table.items():
            body += f"{p} |" + "|".join(rows) + "\n"

        await interaction.response.send_message(
            f"```\n{header}{line}{body}```", ephemeral=True
        )

    @app_commands.command(
        name="add", description="授業を登録します（曜日, 時限, 科目, 教室）"
    )
    @app_commands.describe(
        weekday="曜日を選択", period="時限を選択", subject="科目名", room="教室"
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
        data = load_user_data(user_id)
        term = get_current_term()
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
        await send_dm(
            interaction.user,
            f" 授業を登録しました：{weekday} {period}限 — {subject} ({room})",
        )
        await interaction.followup.send("授業をDMで登録しました。", ephemeral=True)

    @app_commands.command(
        name="remove", description="授業を削除します（曜日＋時限で指定）"
    )
    @app_commands.describe(weekday="曜日を選択", period="時限を選択")
    @app_commands.autocomplete(weekday=weekday_autocomplete, period=period_autocomplete)
    async def class_remove(
        self, interaction: discord.Interaction, weekday: str, period: str
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        term = get_current_term()
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
        await send_dm(
            interaction.user, f" {removed}件を削除しました：{weekday} {period}限"
        )
        await interaction.followup.send("削除結果をDMで送信しました。", ephemeral=True)

    @app_commands.command(
        name="list", description="登録授業（曜日・時限順）をDMで送ります"
    )
    async def class_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = load_user_data(user_id)
        term = get_current_term()
        classes = data.get("classes_by_term", {}).get(term, []) or []
        if not classes:
            await send_dm(interaction.user, "登録授業はありません。")
            await interaction.followup.send(
                "DMを送信しました（授業なし）。", ephemeral=True
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
            rowLabels=[f"{p}限" for p in periods_sorted],
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
                content="登録授業一覧です。", file=discord.File(img_path)
            )
        finally:
            if os.path.exists(img_path):
                os.remove(img_path)
        await interaction.followup.send(
            "登録授業一覧をDMで送信しました。", ephemeral=True
        )

    @app_commands.command(
        name="setroom", description="特定日の特定授業の教室を変更します"
    )
    @app_commands.describe(
        date="YYYY-MM-DD", period="変更する時限", new_room="新しい教室名"
    )
    @app_commands.autocomplete(period=period_autocomplete, new_room=room_autocomplete)
    async def class_setroom(
        self, interaction: discord.Interaction, date: str, period: str, new_room: str
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except Exception:
            await interaction.followup.send(
                "日付形式が無効です。YYYY-MM-DD で指定してください。", ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = load_user_data(user_id)
        term = get_current_term()
        found = False
        for cls in data.get("classes_by_term", {}).get(term, []):
            if str(cls.get("period")) == str(period):
                cls.setdefault("room_overrides", {})[date] = new_room
                found = True
        if not found:
            await interaction.followup.send(
                "指定の授業が見つかりませんでした。", ephemeral=True
            )
            return
        save_user_data(user_id, data)
        await send_dm(
            interaction.user,
            f" {date} の {period}限 の教室を {new_room} に変更しました。",
        )
        await interaction.followup.send("教室変更をDMに送信しました。", ephemeral=True)

    @app_commands.command(
        name="setday", description="自分の特定日の曜日を変更（全授業に適用）"
    )
    @app_commands.describe(date="YYYY-MM-DD", new_weekday="変更後の曜日")
    @app_commands.autocomplete(new_weekday=weekday_autocomplete)
    async def class_setday(
        self, interaction: discord.Interaction, date: str, new_weekday: str
    ):
        await interaction.response.defer(ephemeral=True)
        if new_weekday not in WEEKDAY_MAP:
            await interaction.followup.send("無効な曜日です。", ephemeral=True)
            return
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except Exception:
            await interaction.followup.send(
                "日付形式が無効です。YYYY-MM-DD で指定してください。", ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = load_user_data(user_id)
        term = get_current_term()
        for cls in data.get("classes_by_term", {}).get(term, []):
            cls.setdefault("overrides", {})[date] = WEEKDAY_MAP[new_weekday]
        save_user_data(user_id, data)
        await send_dm(
            interaction.user,
            f" {date} の曜日を {new_weekday} に変更しました（登録授業すべてに適用）。",
        )
        await interaction.followup.send("曜日変更をDMで送信しました。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ClassCog(bot))
