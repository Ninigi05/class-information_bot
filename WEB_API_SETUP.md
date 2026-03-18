# Discord授業情報Bot + Web API - セットアップガイド

FastAPI を Discord Bot に統合し、自宅PC上でAPIサーバーを動作させ、GitHub Pages などの静的HTMLから操作できる構成になりました。

## 📋 ファイル構成

```
discord_information/
├── main.py                      # Discord Bot（修正済み）
├── utils.py                     # ユーティリティ
├── api_security.py              # API_KEY 認証
├── web_api_integration.py       # FastAPI 統合モジュール
├── run.py                       # 統合起動スクリプト
├── schedule_viewer.html         # GitHub Pages 用ビューアー
├── .env                         # 環境変数（ローカル設定）
├── .env.example                 # 環境変数テンプレート
└── requirements.txt             # Python 依存パッケージ
```

## 🚀 セットアップ手順

### 1. 環境変数の設定

```bash
cp .env.example .env
```

`.env` ファイルを編集して以下を設定：

```env
# Discord Bot
DISCORD_TOKEN=your_discord_token_here
GUILD_ID=your_guild_id_here

# Web API セキュリティ
WEB_HOST=0.0.0.0
WEB_PORT=8000
API_KEY=your-secret-api-key-change-this

# CORS 設定（GitHub Pages URL）
CORS_ORIGIN_GITHUB_PAGES=https://username.github.io
```

### 2. API_KEY の生成

安全な API_KEY を生成：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

出力されたキーを `.env` の `API_KEY` に設定してください。

### 3. アプリケーション起動

```bash
python run.py
```

または直接：

```bash
python main.py
```

**起動時のログ:**
```
2026-03-18 10:00:00 INFO [__main__] Web API サーバーを起動します: 0.0.0.0:8000
2026-03-18 10:00:01 INFO [__main__] [INFO] Web API サーバーが起動しました
2026-03-18 10:00:01 INFO [__main__] [INFO] Discord Bot を起動します...
```

## 🌐 GitHub Pages 上での利用

### 1. HTML ファイルの配置

GitHub リポジトリの `docs/` フォルダに `schedule_viewer.html` を配置：

```
your-repo/
└── docs/
    └── schedule_viewer.html
```

### 2. GitHub Pages 設定

リポジトリの Settings > Pages で以下を設定：
- Source: `Deploy from a branch`
- Branch: `main` (or `master`)
- Folder: `/docs`

### 3. アクセス

`https://username.github.io/schedule_viewer.html` でアクセス

## 🔐 セキュリティ設定

### API_KEY 認証

すべてのリクエストに `X-API-Key` ヘッダーが必須です：

```javascript
fetch('http://your-pc-ip:8000/api/user/123456789/classes', {
    headers: {
        'X-API-Key': 'your-secret-api-key'
    }
})
```

### CORS 設定

複数のドメインから API を利用可能にするには `.env` で設定：

```env
CORS_ORIGIN_GITHUB_PAGES=https://username.github.io,https://example.com
```

## 📡 API エンドポイント

### 1. ヘルスチェック

```
GET /api/health
```

レスポンス:
```json
{
    "status": "ok",
    "service": "Discord授業情報Bot API"
}
```

### 2. 授業一覧取得

```
GET /api/user/{user_id}/classes
```

**ヘッダー:** `X-API-Key: your-secret-api-key`

レスポンス:
```json
{
    "user_id": 123456789,
    "count": 5,
    "classes": [
        {
            "day": 0,
            "period": "1",
            "subject": "プログラミング基礎",
            "room": "301教室",
            "weekday_name": "月曜日"
        }
    ],
    "weekdays": ["月曜日", "火曜日", ...],
    "period_to_time": {"1": "09:00", "2": "10:45", ...}
}
```

### 3. 時間割テーブル取得

```
GET /api/user/{user_id}/schedule-table
```

レスポンス:
```json
{
    "user_id": 123456789,
    "table": {
        "月曜日": {
            "1": {"day": 0, "period": "1", "subject": "プログラミング基礎", "room": "301教室"},
            "2": null,
            ...
        },
        ...
    },
    "weekdays": ["月曜日", ...],
    "periods": ["1", "2", "3", "4", "5", "6"],
    "period_to_time": {...}
}
```

### 4. すべてのデータ取得

```
GET /api/user/{user_id}/all-data
```

ユーザーのすべてのデータ（授業、補講、休講、試験時間割等）を取得

### 5. アプリケーション定数取得

```
GET /api/constants
```

曜日、時限情報等の定数データを取得

## 🔧 トラブルシューティング

### 1. CORS エラーが出る

**エラー:** `Access to XMLHttpRequest blocked by CORS policy`

**解決方法:**
- `.env` の `CORS_ORIGIN_GITHUB_PAGES` に GitHub Pages の URL を追加
- または `WEB_HOST` を `0.0.0.0` に設定

```env
CORS_ORIGIN_GITHUB_PAGES=https://username.github.io
```

### 2. API_KEY エラー

**エラー:** `API_KEY が必要です（X-API-Key ヘッダー）`

**解決方法:**
- HTML フォーム上で正しい API_KEY を入力
- `.env` の `API_KEY` と一致しているか確認

### 3. 接続できない

**エラー:** `Failed to fetch`

**解決方法:**
- PC上のアプリケーションが起動しているか確認
- ファイアウォール設定を確認
- `http://localhost:8000/api/health` でローカルテストを実行

### 4. ポートが使用中

**エラー:** `Address already in use`

**解決方法:**
- `.env` で `WEB_PORT` を別のポート番号に変更

```env
WEB_PORT=8001
```

## 📱 JavaScript クライアント例

```javascript
const API_BASE_URL = 'http://your-pc-ip:8000';
const API_KEY = 'your-secret-api-key';
const USER_ID = 'your-discord-user-id';

// 授業一覧を取得
async function getClasses() {
    const response = await fetch(
        `${API_BASE_URL}/api/user/${USER_ID}/classes`,
        {
            headers: {
                'X-API-Key': API_KEY
            }
        }
    );
    const data = await response.json();
    console.log('授業一覧:', data.classes);
}

// 時間割テーブルを取得
async function getScheduleTable() {
    const response = await fetch(
        `${API_BASE_URL}/api/user/${USER_ID}/schedule-table`,
        {
            headers: {
                'X-API-Key': API_KEY
            }
        }
    );
    const data = await response.json();
    console.log('時間割:', data.table);
}
```

## 🛡️ セキュリティのベストプラクティス

1. **API_KEY の保管**
   - `.env` ファイルを `.gitignore` に追加
   - GitHub に絶対にコミットしない

2. **CORS の制限**
   - 必要なドメインのみ許可
   - 本番環境では HTTPS を要求

3. **ファイアウォール設定**
   - 自宅ネットワーク内のみからアクセス可能に設定

4. **定期的な API_KEY 変更**
   - 定期的に `API_KEY` を変更

## 📚 関連ファイル

- [web_api_integration.py](web_api_integration.py) - FastAPI 統合ロジック
- [api_security.py](api_security.py) - API_KEY 認証ロジック
- [schedule_viewer.html](schedule_viewer.html) - ビューアー UI

## ❓ FAQ

**Q: Discord Bot と Web API の両方が同時に動作するのか？**
A: はい。Web API はスレッドで起動され、Discord Bot と並行実行されます。

**Q: リモートからアクセス可能か？**
A: VPN等を使用すれば可能です。セキュリティ設定を厳密にしてください。

**Q: どのポートを使用している？**
A: デフォルトは 8000 です。`.env` の `WEB_PORT` で変更できます。

**Q: API のレスポンスがキャッシュされるか？**
A: いいえ。毎回 JSON ファイルから読み込みます。

---

ご質問やバグ報告は GitHub Issues でお願いします。
