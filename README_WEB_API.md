# 🚀 FastAPI統合版 - Discord授業情報Bot Web API

自宅PC上で実行される FastAPI が提供する Web API エンドポイントにより、GitHub Pages などの静的ホスティングから授業情報を閲覧・操作できます。

Discord Bot と Web API サーバーが**同時に動作**し、同じ JSON データベースを共有します。

## ⚡ クイックスタート

### 1. 環境設定

```bash
# 基本設定のコピー
cp .env.example .env

# APIキーの自動生成
python -c "import secrets; print('API_KEY=' + secrets.token_urlsafe(32))" >> .env
```

### 2. 起動

```bash
python main.py
```

起動ログ:
```
2026-03-18 10:00:00 INFO Web API サーバーを起動します: 0.0.0.0:8000
2026-03-18 10:00:01 INFO Web API サーバーが起動しました
2026-03-18 10:00:01 INFO Discord Bot を起動します...
2026-03-18 10:00:02 INFO Bot 起動完了: YourBotName#1234
```

### 3. テスト

```bash
# ヘルスチェック
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/health

# 授業一覧取得
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/user/123456789/classes
```

## 📊 新しいシステムの構成

```
┌─────────────────────┐
│  GitHub Pages       │  ←→  schedule_viewer.html (静的HTML+JS)
│  (異ドメイン)        │
└──────────┬──────────┘
           │ Fetch API (CORS)
           │ +X-API-Key ヘッダー
           ▼
┌─────────────────────┐
│  自宅PC             │
├─────────────────────┤
│  FastAPI Server     │ (port 8000)
│  ├─ /api/health     │
│  ├─ /api/user/...   │
│  └─ CORS許可        │
├─────────────────────┤
│  Discord Bot        │ (main.py)
│  └─ JSON DB         │ (utils.py)
└─────────────────────┘
```

## 🔑 API キー認証

すべてのリクエストに以下のヘッダーが必須：

```
X-API-Key: your-secret-key
```

**JavaScript例:**
```javascript
fetch('http://localhost:8000/api/user/123456789/classes', {
    headers: {
        'X-API-Key': 'your-secret-key',
        'Content-Type': 'application/json'
    }
})
```

## 📐 新しく作成されたファイル

| ファイル | 説明 |
|---------|------|
| `api_security.py` | API_KEY 認証ロジック |
| `web_api_integration.py` | FastAPI 統合モジュール（CORS設定付き） |
| `schedule_viewer.html` | GitHub Pages 用ビューアー UI |
| `run.py` | 統合起動スクリプト |
| `check_setup.py` | 環境チェックツール |
| `WEB_API_SETUP.md` | 詳細セットアップガイド |

## 🌐 使用例

### ローカルテスト

```bash
# ローカルで schedule_viewer.html を開く
# または
python -m http.server 8000

# http://localhost:8000/schedule_viewer.html でアクセス
```

### GitHub Pages でホスト

1. `schedule_viewer.html` を GitHub リポジトリにコミット
2. Settings > Pages で `/docs` または `/root` フォルダを選択
3. 公開される URL にアクセス
4. 以下の値を入力して読み込み:
   - API ベースURL: `http://your-pc-ip:8000`
   - API_KEY: `.env` の `API_KEY` 値
   - Discord ユーザーID: あなたのユーザーID

## 🔐 セキュリティ機能

✅ **API_KEY 認証** - すべてのエンドポイントで検証
✅ **CORS 制限** - 許可されたドメインのみからアクセス可能
✅ **ローカルファイル保護** - JSON ファイルの直接アクセス不可
✅ **エラーハンドリング** - 詳細なエラー情報を返さない

## 📡 API エンドポイント一覧

| メソッド | エンドポイント | 説明 |
|---------|---------------|------|
| GET | `/api/health` | ヘルスチェック |
| GET | `/api/constants` | 曜日・時限情報 |
| GET | `/api/user/{id}/classes` | 授業一覧 |
| GET | `/api/user/{id}/schedule-table` | 時間割テーブル |
| GET | `/api/user/{id}/all-data` | 全データ |

**全エンドポイントに `X-API-Key` ヘッダーが必須です**

## 🛠️ トラブルシューティング

### CORS エラー
`.env` に GitHub Pages の URL を追加:
```env
CORS_ORIGIN_GITHUB_PAGES=https://username.github.io
```

### ポートが使用中
別のポートを指定:
```env
WEB_PORT=8001
```

### 接続できない
```bash
# 環境チェック
python check_setup.py

# ローカルテスト
curl http://localhost:8000/api/health
```

## 📋 環境変数 (.env)

```env
# Discord
DISCORD_TOKEN=your_token
GUILD_ID=your_guild_id

# Web API
WEB_HOST=0.0.0.0
WEB_PORT=8000
API_KEY=your-secret-api-key

# CORS
CORS_ORIGIN_GITHUB_PAGES=https://username.github.io
```

## 🚀 本番化への注意事項

1. **API_KEY を複雑にする**
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. **CORS を明示的に制限**
   - 信頼できるドメインのみ許可

3. **ファイアウォール設定**
   - 必要な IP のみからのアクセスを許可

4. **ログの監視**
   - `/logs/bot.log` をチェック

## 📚 詳細情報

- [詳細セットアップガイド](WEB_API_SETUP.md)
- [Web API 統合コード](web_api_integration.py)
- [ビューアー HTML](schedule_viewer.html)
- [Windows Quick Tunnel 自動運用](docs/quick_tunnel_windows.md)

## ❓ よくある質問

**Q: 複数のユーザーのデータをアクセスできるのか？**
A: 可能です。`{user_id}` パラメータでユーザーを指定できます。

**Q: リアルタイム同期されるのか？**
A: JSON ファイルからの読み込みなので、変更は即座に反映されます。

**Q: オフラインで使用できるのか？**
A: いいえ。自宅PC のサーバーが起動していて、ネットワークで接続されている必要があります。

**Q: モバイルからアクセスできるのか？**
A: はい。同じネットワーク上の任意のデバイスからアクセス可能です。

---

**サポート:** GitHub Issues でお気軽にお問い合わせください
