"""
Webダッシュボード用 Discord DM通知ヘルパーモジュール
"""

import logging
import asyncio
from utils import send_dm

logger = logging.getLogger(__name__)

# グローバルにBotインスタンスを保持
_bot_instance = None


def set_discord_bot(bot):
    """
    Discord Bot のインスタンスを設定します（setup_hookで呼び出します）
    """
    global _bot_instance
    _bot_instance = bot
    logger.info("[Notification] Discord Bot インスタンスが正常にセットされました。")


def get_discord_bot():
    """
    登録されている Discord Bot インスタンスを取得します
    builtins から最優先で取得し、フォールバックとしてローカル変数を使用します
    """
    import builtins
    builtins_bot = getattr(builtins, "_discord_bot", None)
    if builtins_bot is not None:
        return builtins_bot
    return _bot_instance


async def notify_user_change(user_id: str | int, message: str):
    """
    指定された Discord ユーザーに対して、Web ダッシュボード上での変更を通知する DM を送信します。
    FastAPI のイベントループから Discord Bot のイベントループへスレッドセーフに非同期タスクを送信します。
    """
    try:
        bot = get_discord_bot()
        if bot is None:
            logger.warning(
                "[Notification] Discord bot インスタンスが登録されていません。通知をスキップします。"
            )
            return

        discord_id = int(user_id)
        full_message = f"【Webダッシュボード通知】\n{message}"

        # Discord Bot側のイベントループで実行する非同期コルーチン
        async def _async_send_dm_task():
            try:
                user = bot.get_user(discord_id)
                if user is None:
                    user = await bot.fetch_user(discord_id)

                if user:
                    await send_dm(user, full_message)
                    logger.info(
                        f"[Notification] DM通知を送信しました。対象ユーザー: {discord_id}"
                    )
                else:
                    logger.warning(
                        f"[Notification] ユーザー(ID: {discord_id}) が見つかりませんでした。"
                    )
            except Exception as e:
                logger.exception(
                    f"[Notification] Discord側コルーチン実行中にエラーが発生しました: {e}"
                )

        # Discord Bot のイベントループ上で安全にタスクを実行
        loop = bot.loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(_async_send_dm_task(), loop)
            logger.info(
                f"[Notification] BotのイベントループにDM送信タスクを登録しました。対象ユーザー: {discord_id}"
            )
        else:
            logger.warning(
                "[Notification] Discord Bot のイベントループが稼働していません。通知をスキップします。"
            )

    except Exception as e:
        logger.exception(
            f"[Notification] DM通知タスクの作成中にエラーが発生しました: {e}"
        )


