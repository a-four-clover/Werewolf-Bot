# ngrokを使った閲覧専用サーバーの共有手順

## セキュリティ機能

✅ **実装済み:**
- パスワード認証（必須）
- Rate limiting（15分間に5回までのログイン試行）
- セッションベース認証（24時間有効）
- 読み取り専用エンドポイント（書き込み不可）
- セキュリティヘッダー（XSS, Clickjacking対策）
- クライアントIP追跡（プロキシ対応）

## セットアップ手順

### 1. パスワードの設定（必須）

強力なパスワードを環境変数に設定してください。

**PowerShell:**
```powershell
$env:WW_ACCESS_PASSWORD = "your_strong_password_here"
```

**推奨:** 12文字以上、英数字+記号を含むパスワード

### 2. セッションキーの設定（オプション、長時間使用時）

サーバー再起動後もセッションを維持するため、固定のsecret keyを設定します。

**PowerShell:**
```powershell
$env:FLASK_SECRET_KEY = "your_random_secret_key_here"
```

**生成方法（Python）:**
```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. サーバーの起動

```powershell
python tools/config_server_readonly.py
```

起動時に以下を確認:
- ✅ Password authentication enabled
- ✅ Persistent session key configured

### 4. ngrokトンネルの作成

別のターミナルで:

```powershell
ngrok http 8001
```

ngrokが生成したURL（例: `https://xxxx-xx-xx-xx-xx.ngrok-free.app`）を友人に共有します。

## 使用方法

1. 友人が ngrok URL にアクセス
2. ログイン画面でパスワードを入力
3. 認証成功後、設定を閲覧可能（24時間有効）

## セキュリティ注意事項

### ✅ 安全な使い方

- **強力なパスワードを使用**（12文字以上、推測困難なもの）
- **パスワードを安全に共有**（Discordの秘密チャンネル、暗号化メッセンジャー等）
- **使用後はサーバーを停止**（Ctrl+C）
- **定期的にパスワードを変更**

### ⚠️ リスクと制限事項

1. **平文パスワード送信のリスク**
   - ngrokは自動的にHTTPSを使用するため、通信は暗号化されます
   - ただし、パスワードが漏洩した場合は不正アクセスの可能性があります

2. **Rate limiting回避**
   - 複数IPから攻撃される可能性（現在IP単位でrate limiting）
   - 対策: 短時間使用後にサーバーを停止する

3. **ngrok無料版の制限**
   - URLが毎回変わる（Pro版は固定URL可能）
   - 接続数制限あり

4. **セッションハイジャックのリスク**
   - セッションCookieが盗まれた場合、不正アクセス可能
   - 対策: 信頼できるネットワークのみで使用

### 🔒 さらなるセキュリティ強化（オプション）

1. **IPホワイトリスト追加**
   ```python
   # config_server_readonly.py に追加
   ALLOWED_IPS = {'友人のIP1', '友人のIP2'}
   ```

2. **アクセスログの監視**
   ```python
   # すべてのアクセスをログに記録
   import logging
   logging.basicConfig(filename='access.log', level=logging.INFO)
   ```

3. **セッション有効期限の短縮**
   ```python
   # 24時間 → 1時間に変更
   app.permanent_session_lifetime = timedelta(hours=1)
   ```

## トラブルシューティング

### エラー: "Too many login attempts"
- 15分間待ってから再試行
- または、サーバーを再起動してカウンターをリセット

### エラー: "authentication_required"
- セッションが期限切れ。再ログインしてください
- サーバーが再起動された場合も発生（FLASK_SECRET_KEY未設定時）

### ngrokが接続できない
- サーバーが `0.0.0.0:8001` でリッスンしていることを確認
- ファイアウォールでポート8001が許可されているか確認

## 停止方法

1. ngrokを停止: `Ctrl+C`
2. サーバーを停止: `Ctrl+C`
3. 環境変数をクリア（オプション）:
   ```powershell
   Remove-Item Env:\WW_ACCESS_PASSWORD
   Remove-Item Env:\FLASK_SECRET_KEY
   ```

## まとめ

この設定により、閲覧専用サーバーを比較的安全にngrok経由で共有できます。
ただし、完全なセキュリティを保証するものではないため:

- **短時間の使用**（数時間程度）
- **信頼できる友人のみ**
- **使用後は必ず停止**

を心がけてください。
