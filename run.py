#!/usr/bin/env python3
"""
Discord授業情報Bot + Web API 統合起動スクリプト
Bot と Web API サーバーを同時に起動します
"""

import os
import sys
import logging

# 環境変数が設定されているかチェック
if not os.path.exists(".env"):
    print("[WARNING] .env ファイルが見つかりません")
    print("          .env.example を参考に .env を作成してください")
    sys.exit(1)

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

logger.info("=" * 60)
logger.info("Discord授業情報Bot + Web API サーバー統合起動")
logger.info("=" * 60)

# main.py を実行
try:
    logger.info("[INFO] アプリケーションを起動中...")
    import main
except KeyboardInterrupt:
    logger.info("[INFO] ユーザーの要求により終了します")
    sys.exit(0)
except Exception as e:
    logger.exception(f"[ERROR] 起動エラー: {e}")
    sys.exit(1)
