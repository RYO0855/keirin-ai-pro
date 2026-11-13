# 公開手順

## 画面

GitHub Pages: main / (root)。index.htmlだけで既存のブラウザ機能とyomisuji参照が動きます。サーバーはPages上では実行できません。

## API

Python 3.12のHTTPSサーバーまたはDocker対応のホストを別途用意します。ホスティング契約・ドメイン・TLS設定は未実施。永続ディスクと認証キーが必要です。

```sh
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
# 出力を.envのKEIRIN_API_KEYへ保存（GitHub/チャットへ貼らない）
# KEIRIN_ALLOWED_ORIGINSはPagesのオリジン https://ryo0855.github.io
# パス /keirin-ai-pro/ はオリジンに含めない
# .envを設定後:
docker compose up -d --build
```

composeは127.0.0.1:8000のみ公開します。HTTPSリバースプロキシからそのポートへ接続してください。ホスト提供のコンテナサービスならbackend/Dockerfileをリポジトリルートのコンテキストでビルドし、0.0.0.0:8000へルーティング。環境変数KEIRIN_API_KEY、KEIRIN_ALLOWED_ORIGINS、KEIRIN_DB=/data/keirin.sqliteを設定します。UID10001が書き込める永続ディスクを/dataへマウントします。無料の一時ディスクでは再起動時に履歴を失います。

ローカル起動：

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
# シェルでKEIRIN_API_KEYとKEIRIN_ALLOWED_ORIGINSを設定
uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers
```

.envはcompose用。直接uvicornを起動する場合は環境変数を別途exportしてください。SQLiteとプロセス内の学習ロック/レート制限を使うため1ワーカーで運用。大規模化時は専用DB・ジョブキュー・共有レート制限へ移行が必要です。

画面の「接続・運用」でAPIオリジンと認証キーを入力。APIキーはページ内メモリのみ保持し、localStorageには保存しません。単一運用者向け共有キー方式です。一般公開の多人数サービス向けOIDC/RBACは未実装。キーを一般利用者へ配らないでください。

APIの/healthで設定・モデル状態を確認 → 正規の許諾情報を登録 → 許可データを取り込み → 選択日の許可データを表示。学習条件が揃った後に学習・評価し、レポート確認後に運用承認します。空DBや未承認モデルはケンのままです。

## バックアップ・セキュリティ

SQLiteのDB・WALを稼働中に単純コピーせず、sqlite3.Connection.backupで整合性のあるバックアップを作成してください。バックアップと復元の運用は提供先で設定が必要です。端末データは従来のJSON書き出しを利用します。サーバーへ端末メモを自動送信しません。

APIはBearer必須（health以外）、明示CORS、5MB制限、厳格スキーマ、パラメーター化SQL、追記専用履歴、重複防止、入力レート制限。鍵やDBをGitに追加しないでください。Dockerは非root、capabilities削除、read-only root。コンテナの実ビルド・本番TLS・実機Safariの動作は未検証です。

## GitHubへの反映

書き込み権限を有効にした接続から、この作業ブランチをpushしてPRを作成し、テスト後にmainへマージしてください。403が出た場合は同じ操作を繰り返さず、GitHub連携のContents/Workflows権限を見直します。CIワークフローを含むためWorkflows権限が必要になる場合があります。
