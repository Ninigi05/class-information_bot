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
    populate_exam_schedule,
    add_exam_class,
    remove_exam_class,
    WEEKDAYS,
    WEEKDAY_MAP,
    PERIOD_TO_TIME,
)


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
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
            or self.user_cache[user_id]["mtime"] != current_mtime
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
        name="set_time", description="試験の時限別開始時間を登録します"
    )
    @app_commands.describe(period="設定する時限（例：1）", time="開始時刻（例：09:00）")
    @app_commands.autocomplete(period=period_autocomplete)
    async def set_exam_period_time(
        self, interaction: discord.Interaction, period: str, time: str
    ):
        if period not in PERIOD_TO_TIME:
            await interaction.response.send_message(
                f"時限は {'・'.join(PERIOD_TO_TIME)} のいずれかを指定してください。",
                ephemeral=True,
            )
            return
        if not re.match(r"^(2[0-3]|[01]?\d):[0-5]\d$", time):
            await interaction.response.send_message(
                "時刻は「HH:MM」の形式で入力してください（例：09:00）。時刻は0〜23時、分は0〜59分で入力して下さい。",
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
            f"試験時の{period}限の開始時刻を{time}に設定しました。", ephemeral=True
        )

    async def reset_period_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        options = list(PERIOD_TO_TIME.keys()) + ["all"]
        return [app_commands.Choice(name=p, value=p) for p in options if current in p]

    @app_commands.command(
        name="reset_time", description="試験期間中の時限の開始時刻を元に戻します"
    )
    @app_commands.describe(period="元に戻す時限（例：1）、all で全時限")
    @app_commands.autocomplete(period=reset_period_autocomplete)
    async def reset_exam_period_time(
        self, interaction: discord.Interaction, period: str
    ):
        user_id = interaction.user.id
        data = self.get_data(user_id)
        overrides = data.get("exam_period_overrides", {}) or {}
        if period.lower() == "all":
            data["exam_period_overrides"] = {}
            save_user_data(user_id, data)
            await interaction.response.send_message(
                "試験期間中の全時限の開始時刻を元に戻しました。", ephemeral=True
            )
            return
        if period not in overrides:
            await interaction.response.send_message(
                f"試験期間中の{period}限には設定がありません。", ephemeral=True
            )
            return
        del overrides[period]
        data["exam_period_overrides"] = overrides
        save_user_data(user_id, data)
        # 試験期間中の開始時刻は /setting period_time → 初期値の順で決まる
        fallback = (data.get("period_overrides", {}) or {}).get(
            period
        ) or PERIOD_TO_TIME.get(period, "不明")
        await interaction.response.send_message(
            f"試験期間中の{period}限の開始時刻を元に戻しました（{fallback}）。",
            ephemeral=True,
        )

    @app_commands.command(
        name="edit", description="試験時間割の名前・期間を変更します"
    )
    @app_commands.describe(
        name="変更する時間割名",
        new_name="（任意）新しい時間割名",
        start="（任意）新しい開始日 (YYYY-MM-DD)",
        end="（任意）新しい終了日 (YYYY-MM-DD)",
    )
    @app_commands.autocomplete(name=exam_name_autocomplete)
    async def exam_edit(
        self,
        interaction: discord.Interaction,
        name: str,
        new_name: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        if not (new_name or start or end):
            await interaction.followup.send(
                "変更する項目（new_name・start・end）を指定してください。", ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        schedules = data.get("exam_schedules_by_term", {}).get(term, []) or []
        target = next((s for s in schedules if s.get("name") == name), None)
        if not target:
            await interaction.followup.send(
                "該当の時間割が見つかりませんでした。", ephemeral=True
            )
            return
        new_name = (new_name or "").strip() or name
        if new_name != name and any(s.get("name") == new_name for s in schedules):
            await interaction.followup.send(
                "同名の試験時間割が既に存在します。", ephemeral=True
            )
            return
        new_start = start or target.get("start")
        new_end = end or target.get("end")
        sd, ed = _parse_date(new_start), _parse_date(new_end)
        if sd is None or ed is None:
            await interaction.followup.send(
                "日付形式が無効です。YYYY-MM-DD で指定してください。", ephemeral=True
            )
            return
        if sd > ed:
            await interaction.followup.send(
                "開始日は終了日より前にしてください。", ephemeral=True
            )
            return
        old = f"{name}（{target.get('start')} 〜 {target.get('end')}）"
        target.update({"name": new_name, "start": new_start, "end": new_end})
        save_user_data(user_id, data)
        await send_dm(
            interaction.user,
            f"試験時間割を変更しました: {old} → {new_name}（{new_start} 〜 {new_end}）",
        )
        await interaction.followup.send("試験時間割を変更しました（DM送付）。", ephemeral=True)

    @app_commands.command(
        name="create", description="試験用時間割を作成します（名前・期間）"
    )
    @app_commands.describe(
        name="時間割名", start="開始日 (YYYY-MM-DD)", end="終了日 (YYYY-MM-DD)"
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
                "日付形式が無効です。YYYY-MM-DD で指定してください。", ephemeral=True
            )
            return
        if sd > ed:
            await interaction.followup.send(
                "開始日は終了日より前にしてください。", ephemeral=True
            )
            return
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        data.setdefault("exam_schedules_by_term", {}).setdefault(term, [])
        for s in data["exam_schedules_by_term"][term]:
            if s.get("name") == name:
                await interaction.followup.send(
                    "同名の試験時間割が既に存在します。", ephemeral=True
                )
                return
        sched = {"name": name, "start": start, "end": end, "classes": []}
        # 定期試験ありの授業を、同じ曜日・時限で自動追加する
        added = populate_exam_schedule(data, term, sched)
        data["exam_schedules_by_term"][term].append(sched)
        save_user_data(user_id, data)
        await send_dm(
            interaction.user,
            f"試験時間割「{name}」を作成しました: {start} 〜 {end}\n"
            f"定期試験ありの授業 {added}件を自動で追加しました（/exam show で確認できます）",
        )
        await interaction.followup.send(
            "試験時間割をDMで作成しました。", ephemeral=True
        )

    @app_commands.command(name="delete", description="指定した試験時間割を削除します")
    @app_commands.describe(name="削除する時間割名")
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
            interaction.user, f"🗑️ 試験時間割「{name}」を削除しました（{removed}件）"
        )
        await interaction.followup.send("削除結果をDMで送信しました。", ephemeral=True)

    @app_commands.command(
        name="list", description="登録済み試験時間割の一覧をDMで表示します"
    )
    async def exam_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        data = self.get_data(user_id)
        term = get_effective_term(user_id)
        schedules = data.get("exam_schedules_by_term", {}).get(term, []) or []
        if not schedules:
            await send_dm(interaction.user, "試験時間割は登録されていません。")
            await interaction.followup.send(
                "DMを送信しました（試験時間割なし）。", ephemeral=True
            )
            return
        lines = ["登録済み試験時間割:"]
        for s in sorted(schedules, key=lambda x: x.get("start", "")):
            lines.append(
                f"- {s.get('name')} : {s.get('start')} 〜 {s.get('end')} ({len(s.get('classes', []))}件)"
            )
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send(
            "試験時間割一覧をDMで送信しました。", ephemeral=True
        )

    @app_commands.command(
        name="show", description="指定した試験時間割の中身を表示します"
    )
    @app_commands.describe(name="表示する時間割名")
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
                "該当の時間割が見つかりませんでした。", ephemeral=True
            )
            return
        classes = target.get("classes", []) or []
        if not classes:
            await send_dm(
                interaction.user, f"試験時間割「{name}」には授業が登録されていません。"
            )
            await interaction.followup.send(
                "DMを送信しました（授業なし）。", ephemeral=True
            )
            return
        lines = [f"試験時間割「{name}」の授業:"]
        classes_sorted = sorted(
            classes,
            key=lambda x: (
                x.get("day", 0),
                int(x.get("period", 0) if str(x.get("period")).isdigit() else 999),
            ),
        )
        for c in classes_sorted:
            wd = WEEKDAYS[int(c.get("day"))] if c.get("day") is not None else "不明曜日"
            period_display = c.get("period") or c.get("time") or "?"
            auto = "（時間割から自動追加）" if c.get("from_class") else ""
            lines.append(
                f"{wd} {period_display}限 : {c.get('subject', '')} ({c.get('room', '未設定')}){auto}"
            )
        await send_long_dm(interaction.user, "\n".join(lines))
        await interaction.followup.send(
            "試験時間割をDMで送信しました。", ephemeral=True
        )

    @app_commands.command(name="addclass", description="試験時間割に授業を追加します")
    @app_commands.describe(
        name="時間割名",
        weekday="曜日",
        period="時限",
        subject="科目名",
        room="教室",
        time="（任意）開始時刻 HH:MM",
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
                "該当の時間割が見つかりませんでした。", ephemeral=True
            )
            return
        if weekday not in WEEKDAY_MAP or period not in PERIOD_TO_TIME:
            await interaction.followup.send(
                "曜日・時限は候補から選択してください。", ephemeral=True
            )
            return
        if time and not re.match(r"^(2[0-3]|[01]?\d):[0-5]\d$", time):
            await interaction.followup.send(
                "時刻は「HH:MM」の形式で入力してください（例：09:00）。", ephemeral=True
            )
            return
        day_idx = WEEKDAY_MAP[weekday]
        time_val = time or PERIOD_TO_TIME.get(period)
        entry = {
            "day": day_idx,
            "period": period,
            "time": time_val,
            "subject": subject,
            "room": room,
        }
        # 同じ曜日・時限の既存分（自動追加分を含む）は置き換える
        add_exam_class(target, entry)
        save_user_data(user_id, data)
        await send_dm(
            interaction.user,
            f"試験時間割「{name}」に授業を追加しました: {weekday} {period}限 {subject} ({room})",
        )
        await interaction.followup.send(
            "試験時間割に授業を追加しました（DM送付）。", ephemeral=True
        )

    @app_commands.command(
        name="removeclass",
        description="試験時間割から授業を削除します（曜日・時限で指定）",
    )
    @app_commands.describe(name="時間割名", weekday="曜日", period="時限")
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
                "該当の時間割が見つかりませんでした。", ephemeral=True
            )
            return
        day_idx = WEEKDAY_MAP.get(weekday)
        # 自動追加分を削除した場合は、時間割を編集しても再追加しない
        deleted = remove_exam_class(target, day_idx, period)
        save_user_data(user_id, data)
        await send_dm(
            interaction.user,
            f"試験時間割「{name}」から {weekday} {period}限を削除しました（{deleted}件）。",
        )
        await interaction.followup.send("削除結果をDMで送信しました。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ExamCog(bot))
