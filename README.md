# KEIRIN AI PRO v2

既存の選手メモ・連係記録・本線2車単4点/3連単10点/穴目8点を維持した静的フロントエンドと、別ホスト用FastAPI/SQLiteバックエンドです。

**全国の実開催・最新オッズ配信と実データで検証済みのモデルは未接続です。完成済みの実予想サービスではありません。** 架空値を実データとして補完せず、未取得/未実装/ケンを表示します。既存サンプルは明示的に架空です。

実装済み：許諾登録と型付きデータ取り込み、出走表/成績/結果/ライン/追走と所属関係の分析、追記専用の知識時刻、根拠確認付きメモ、日付分割の順位モデル学習・温度校正・ホールドアウト評価、承認ゲート、新しいオッズからの期待値候補、実際の事前保存記録による仮想払戻集計、認証/CORS/容量制限/エラー処理。

未接続：利用許可付きの全国配信API、提供者固有の自動取得アダプター、スケジュール収集、実データで学習済みのモデル。未実装：走行力学/風/先行争いを含む展開モデル、同着/部分返還の精算、一般公開用の多人数認証、大規模学習ジョブキュー、端末メモとの自動同期。本番ホスト・TLS・バックアップ運用の設定も必要です。

- docs/SOURCE_AUDIT.md — 公式API/利用条件の確認結果
- docs/DATA_CONTRACT.md — 許可データと根拠確認のAPI契約
- docs/MODEL_AND_BACKTEST.md — 未来情報対策・校正・検証の限界
- docs/DEPLOY.md — GitHub Pagesと別APIサーバーの公開手順

## テスト

```sh
pip install -r backend/requirements-dev.txt
npm install --ignore-scripts
npx playwright install chromium-headless-shell
python -m unittest discover -s tests -p 'test_backend.py' -v
npm test
npm run test:browser
npm run test:platform
```

テストは全て人工と明示した隔離データ。実配信での予測精度/収益性を示すものではありません。platformテストは実際のローカルAPIを起動し、モバイル幅のブラウザから認証・CORS・許可取り込み・メモ・22枠・欠損時のケン・仮想検証を操作します。WebKitはライブラリ不足で起動不可、実機iPhone Safariは未確認です。
