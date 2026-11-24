import os
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks

import gmail_oauth
from mail_service import (
    fetch_cancellation_emails,
    scan_mail,
    has_gmail_token,
    list_linked_user_ids,
    unlink_gmail,
    now_local,
)
from utils import (
    load_user_data,
    save_user_data,
    send_dm,
    send_long_dm,
    set_day_override,
    add_cancelled_class,
    WEEKDAY_MAP,
)

logger = logging.getLogger(__name__)
GMAIL_CREDENTIALS = gmail_oauth.resolve_credentials_path()
SCAN_INTERVAL_MINUTES = max(5, int(os.getenv("MAIL_SCAN_INTERVAL_MINUTES", "30")))
# 自動スキャンの結果に付けるボタンの有効時間（Bot を再起動すると無効になる）
AUTO_SCAN_VIEW_TIMEOUT = 24 * 60 * 60


def _format_results(results: list[dict]) -> list[str]:
    lines = []
    for item in results:
        date_display = item.get("date") or "不明日付"
        if item.get("type") == "schedule_change":
            lines.append(f"【授業変更】{date_display} → {item.get('target_weekday')} の時間割")
        else:
            period_display = item.get("period") or "?"
            subj = item.get("subject") or item.get("subject_header") or "（不明）"
            lines.append(f"【休講】{date_display} {period_display}限 {subj}")
        sender = item.get("sender")
        if sender:
            lines.append(f"  送信者: {sender}")
            if item.get("is_official_sender") is False:
                lines.append(
                    "  ⚠ 公式リスト外の送信者です。大学からの正式な連絡かどうか、内容をよく確認してください"
                )
    return lines


def _actions_from_results(results: list[dict]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """メールの解析結果から、反映できる授業変更日（日付→曜日）と休講（日付, 時限）を取り出す。"""
    changes = {
        item["date"]: item["target_weekday"]
        for item in results
        if item.get("type") == "schedule_change"
        and item.get("date")
        and item.get("target_weekday") in WEEKDAY_MAP
    }
    cancels = sorted(
        {
            (item["date"], period)
            for item in results
            if item.get("type") == "cancellation" and item.get("date")
            for period in item.get("matched_periods") or []
        }
    )
    return changes, cancels


def _action_prompt(changes: dict[str, str], cancels: list[tuple[str, str]]) -> str:
    lines = ["次の内容を時間割に反映しますか？（メール内容を確認してから押してください）"]
    lines += [f"・授業変更: {d} → {wd} の時間割" for d, wd in sorted(changes.items())]
    lines += [f"・休講: {d} {p}限" for d, p in cancels]
    return "\n".join(lines)


class MailActionView(discord.ui.View):
    """メールで見つかった授業変更日・休講を、本人の確認後に時間割へ反映するボタン。

    誤判定の可能性があるため、自動では反映しない。
    """

    def __init__(
        self,
        user_id: int,
        changes: dict[str, str],
        cancels: list[tuple[str, str]],
        timeout: float = 600,
    ):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.changes = changes
        self.cancels = cancels
        if not changes:
            self.remove_item(self.apply_changes)
        if not cancels:
            self.remove_item(self.apply_cancels)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    def _finish_if_done(self):
        if all(item.disabled for item in self.children if item is not self.dismiss):
            for item in self.children:
                item.disabled = True
            self.stop()

    @discord.ui.button(label="授業変更を反映する", style=discord.ButtonStyle.primary)
    async def apply_changes(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_data = load_user_data(self.user_id)
        for date_str, target_wd in self.changes.items():
            set_day_override(user_data, date_str, target_wd)
        save_user_data(self.user_id, user_data)
        button.disabled = True
        self._finish_if_done()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"授業変更日（{len(self.changes)}件）を時間割に反映しました。"
            "取り消すときは /class unsetday を使ってください。",
            ephemeral=True,
        )

    @discord.ui.button(label="休講を登録する", style=discord.ButtonStyle.primary)
    async def apply_cancels(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_data = load_user_data(self.user_id)
        added, skipped = [], []
        for date_str, period in self.cancels:
            try:
                add_cancelled_class(user_data, date_str, period)
                added.append(f"{date_str} {period}限")
            except ValueError as e:
                skipped.append(f"{date_str} {period}限（{e}）")
        save_user_data(self.user_id, user_data)
        button.disabled = True
        self._finish_if_done()
        await interaction.response.edit_message(view=self)
        msg = f"休講を{len(added)}件登録しました。"
        if skipped:
            msg += "\n登録しなかったもの:\n" + "\n".join(f"・{s}" for s in skipped)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="反映しない", style=discord.ButtonStyle.secondary)
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.stop()
        await interaction.response.edit_message(view=self)


class MailCog(commands.GroupCog, name="mail"):
    """Gmail 認証・休講メールの取得と自動スキャン"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()
        self.auto_scan_loop.change_interval(minutes=SCAN_INTERVAL_MINUTES)
        self.auto_scan_loop.start()

    def cog_unload(self):
        self.auto_scan_loop.cancel()

    # ============ 自動スキャン ============

    @tasks.loop(minutes=30)
    async def auto_scan_loop(self):
        for user_id in list_linked_user_ids():
            try:
                await self._scan_user(user_id)
            except Exception as e:
                logger.exception(f"[MailScan] 自動スキャン失敗 user={user_id}: {e}")

    @auto_scan_loop.before_loop
    async def before_auto_scan_loop(self):
        await self.bot.wait_until_ready()

    async def _scan_user(self, user_id: int):
        data = load_user_data(user_id)
        if data.get("mail_auto_scan", True) is False:
            return
        seen = data.get("mail_seen_ids", []) or []
        # Gmail API は同期通信のため、イベントループを止めないよう別スレッドで実行する
        result = await asyncio.to_thread(scan_mail, user_id, seen)

        if result is None:
            # トークンが失効している。何度も送らないよう、再連携するまで1回だけ知らせる
            if not data.get("mail_auth_error_notified"):
                user = await self._get_user(user_id)
                if user:
                    await send_dm(
                        user,
                        "【Gmail自動スキャン】Gmail 連携の有効期限が切れたため、メールを確認できませんでした。\n"
                        "/mail auth からもう一度連携してください。",
                    )
                data["mail_auth_error_notified"] = True
                save_user_data(user_id, data)
            return

        results, message_ids = result
        # 過去の日付の通知は知らせない
        today = now_local().strftime("%Y-%m-%d")
        results = [r for r in results if (r.get("date") or "") >= today]

        changed = data.get("mail_seen_ids") != message_ids or data.get(
            "mail_auth_error_notified"
        )
        # 検索期間（14日）内のメールだけを記録すれば、同じメールを二度知らせない
        data["mail_seen_ids"] = message_ids
        data.pop("mail_auth_error_notified", None)
        if changed:
            save_user_data(user_id, data)

        if not results:
            return
        user = await self._get_user(user_id)
        if user is None:
            return
        logger.info(f"[MailScan] user={user_id} 新しい通知 {len(results)}件")
        await send_long_dm(
            user,
            "\n".join(
                ["【Gmail自動スキャン】新しい休講・授業変更のメールが見つかりました:"]
                + _format_results(results)
            ),
        )
        changes, cancels = _actions_from_results(results)
        if changes or cancels:
            try:
                dm = await user.create_dm()
                await dm.send(
                    _action_prompt(changes, cancels),
                    view=MailActionView(
                        user_id, changes, cancels, timeout=AUTO_SCAN_VIEW_TIMEOUT
                    ),
                )
            except Exception as e:
                logger.warning(f"[MailScan] 確認ボタンの送信失敗 user={user_id}: {e}")

    async def _get_user(self, user_id: int):
        user = self.bot.get_user(user_id)
        if user is not None:
            return user
        try:
            return await self.bot.fetch_user(user_id)
        except Exception as e:
            logger.warning(f"[MailScan] ユーザー取得失敗 user={user_id}: {e}")
            return None

    # ============ コマンド ============

    @app_commands.command(
        name="auth", description="Gmail 認証フローを開始します（DMでURL送付）"
    )
    async def mail_auth(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        try:
            # 発行した利用者だけがコードを交換できるよう、user_id に紐付けて発行する
            auth_url, _ = gmail_oauth.create_auth_url(GMAIL_CREDENTIALS, user_id)
            await send_dm(
                interaction.user,
                f"Gmail認証を開始します。\n以下のURLを開き、表示された認証コード（code）をコピーしてください：\n\n{auth_url}"
                f"\n\nコピーしたコードは `/mail setcode <認証コード>` で入力してください。",
            )
            await interaction.followup.send("認証URLをDMに送信しました。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"認証開始に失敗しました: {e}", ephemeral=True)

    @app_commands.command(
        name="setcode", description="Gmail 認証コードを入力して連携を完了します"
    )
    @app_commands.describe(code="認証コード")
    async def mail_setcode(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        try:
            # /mail auth で本人に発行した code_verifier と照合して交換する（通信を伴うため別スレッドで実行）
            await asyncio.to_thread(
                gmail_oauth.exchange_code, GMAIL_CREDENTIALS, user_id, code
            )
        except gmail_oauth.GmailAuthError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        await send_dm(
            interaction.user,
            "【Gmail連携完了通知】\n\nGmail 認証が正常に完了しました！\n"
            f"今後は約{SCAN_INTERVAL_MINUTES}分ごとに Gmail を確認し、休講・授業変更のメールが届いたら DM でお知らせします。\n"
            "自動確認を止めるときは `/mail autoscan enabled:False`、連携を解除するときは `/mail unlink` を使ってください。",
        )
        await interaction.followup.send("認証完了しました。", ephemeral=True)

    @app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    @app_commands.command(
        name="fetch",
        description="Gmailから休講・授業変更情報を取得してDMで表示（確認後に時間割へ反映）",
    )
    async def mail_fetch(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        try:
            results = await asyncio.to_thread(fetch_cancellation_emails, user_id)
            if not results:
                await send_dm(
                    interaction.user, "該当する休講・授業変更情報は見つかりませんでした。"
                )
                await interaction.followup.send("取得完了（該当なし）。", ephemeral=True)
                return

            await send_long_dm(
                interaction.user,
                "\n".join(["最新の通知情報:"] + _format_results(results)),
            )
            changes, cancels = _actions_from_results(results)
            if changes or cancels:
                await interaction.followup.send(
                    "通知情報をDMで送信しました。\n\n" + _action_prompt(changes, cancels),
                    view=MailActionView(user_id, changes, cancels),
                    ephemeral=True,
                )
                return
            await interaction.followup.send("通知情報をDMで送信しました。", ephemeral=True)
        except Exception as e:
            logger.exception(f"[ERROR] mail_fetch error: {e}")
            await interaction.followup.send(
                f"休講情報の取得に失敗しました: {e}", ephemeral=True
            )

    @app_commands.command(
        name="autoscan", description="Gmail の自動確認（新着の休講・授業変更をDMで通知）を切り替えます"
    )
    @app_commands.describe(enabled="True で自動確認する、False で止める")
    async def mail_autoscan(self, interaction: discord.Interaction, enabled: bool):
        user_id = interaction.user.id
        data = load_user_data(user_id)
        data["mail_auto_scan"] = enabled
        save_user_data(user_id, data)
        if enabled and not has_gmail_token(user_id):
            msg = "自動確認を有効にしました。Gmail が未連携のため、/mail auth で連携すると確認を始めます。"
        elif enabled:
            msg = f"自動確認を有効にしました（約{SCAN_INTERVAL_MINUTES}分ごと）。"
        else:
            msg = "自動確認を停止しました。/mail fetch で手動確認はできます。"
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(
        name="unlink", description="Gmail 連携を解除し、保存している認証情報を削除します"
    )
    async def mail_unlink(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        removed = await asyncio.to_thread(unlink_gmail, user_id)
        data = load_user_data(user_id)
        for key in ("mail_seen_ids", "mail_auth_error_notified"):
            data.pop(key, None)
        save_user_data(user_id, data)
        await interaction.followup.send(
            "Gmail 連携を解除し、保存していた認証情報を削除しました。"
            if removed
            else "Gmail は連携されていません。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MailCog(bot))
