# 許可データの取り込み契約

APIは契約先の非公開仕様を推測せず、認証付きJSON取り込みを提供します。自動収集アダプター・定期取得・公式最新オッズ接続は未実装です。提供者の仕様・利用許諾を得てから、この形式へ変換する個別アダプターが必要です。

1. `/v1/sources` へ id, name, permission_reference, permitted_uses（display, analysis、必要ならtraining）, operator_attests_permission=true を登録。
2. `/v1/import` へ下記を送信。5MB、1回2000レコードまで。同一内容は重複排除し、1件でも不正ならそのバッチ全体をロールバック。
3. 許諾が失効したら `/v1/sources/{id}/disable` をPOST。履歴は監査用に残し、表示・分析・そのソースを使用する承認済みモデルを停止。再許可は新ソースIDで登録。

`operator_attests_permission` は運用者の申告です。サーバーが契約を法的に認証したことにはなりません。

```json
{
  "source_id": "your-licensed-source",
  "records": [
    {
      "kind": "race",
      "race_id": "provider-race-id",
      "effective_at": "2026-11-13T10:00:00+09:00",
      "payload": {
        "venue": "許可データの開催場",
        "region": "関東",
        "start": "2026-11-13T15:00:00+09:00"
      }
    }
  ]
}
```

上記は形式説明であり、実開催データではありません。effective_atは公開/有効になった日時で未来は不可。将来の発走時刻はpayload.startへ記載。known_atはサーバーの実取込時刻で変更不可。過去に遡るknown_atの入力欄はありません。

kindごとのpayloadは次のとおりです。未記載の項目は拒否します。詳細はbackend/domain.py。

| kind | payload |
|---|---|
| race | venue, region, start（タイムゾーン付きISO8601） |
| roster | entries配列：id, no(1〜9), name, prefecture(null可), race_points(null可) |
| lineup | lines配列：選手IDの配列をライン順に並べる |
| stats | player_id, starts, wins, top3, period_end（発走より前の成績期間） |
| odds | type(2車単/3連単), selection(車番の1-2/1-2-3), multiplier, quoted_at |
| result | order(選手IDを確定着順に), payouts配列(type, selection, per100), result_at, cancelled |
| follow | leader, follower, position(2=番手), followed(true/false/null), dropped(true/false/null), leader_pref(null可), follower_pref(null可), evidence_reference, result_at |

statsもrace_id欄を必須とします。選手共通の資料なら `stats-player-id` などの識別子を指定できます。成績検索はpayload.player_idで行います。oddsのeffective_atはquoted_at、result/followのeffective_atはresult_atと一致させます。result/followはレース登録後・発走以降にのみ登録できます。発走時刻は結果確定時刻の代わりには使いません。

ライン全員が出走表に存在しない場合はラインを欠損として扱います。未所属・欠損のまま確率を作りません。成績の欠損も学習対象から除外。追走結果のnullは率の分母から除外。モデル内部の追走欠損は値0+既知マスク0で符号化し、実測の0%とは区別します。

## メモの確認

`POST /v1/notes`（作成）または `PUT /v1/notes/{id}`（改訂）へ player_id, content, evidence_reference, evidence_excerpt を送ります。改訂は新しいリビジョンになり、以前の確認は引き継ぎません。

`POST /v1/notes/{id}/review` に revision, content_sha256, reviewed_evidence_reference, reviewer を送信。現在の本文ハッシュと根拠参照が一致する場合のみ運用者確認済みとします。外部根拠を自動で真偽判定する機能はありません。運用者が資料を実際に確認する必要があります。任意URLをサーバーに取得させる機能はなく、SSRFの入口を設けません。メモは確率モデルに一切入力しません。

ブラウザ既存の選手メモ・連係記録・4/10/8設定は既存localStorageキーで維持します。サーバーメモとは別管理で、自動アップロードしません。バックエンドのメモ審査APIを使う場合は別途登録します。
