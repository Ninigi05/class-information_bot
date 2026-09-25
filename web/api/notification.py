"""
Webダッシュボード用 Discord DM通知ヘルパーモジュール
"""

import logging
from utils import send_dm

logger = logging.getLogger(__name__)


async def notify_user_change(user_id: str | int, message: str):
    """
    指定された Discord ユーザーに対して、Web ダッシュボード上での変更を通知する DM を送信します。
    """
    try:
        from main import get_discord_bot

        bot = get_discord_bot()
        if bot is None:
            logger.warning(
                "[Notification] Discord bot インスタンスが取得できませんでした。通知をスキップします。"
            )
            return

        discord_id = int(user_id)
        user = bot.get_user(discord_id)
        if user is None:
            user = await bot.fetch_user(discord_id)

        if user:
            full_message = f"【Webダッシュボード通知】\n{message}"
            await send_dm(user, full_message)
            logger.info(
                f"[Notification] DM通知を送信しました。対象ユーザー: {user_id}"
            )
        else:
            logger.warning(
                f"[Notification] ユーザー(ID: {user_id}) が見つかりませんでした。"
            )
    except Exception as e:
        logger.exception(
            f"[Notification] DM通知送信中にエラーが発生しました: {e}"
        )
