# ポート台帳(写し)

正本は Notion の「ポート管理」データベース。ポートを追加・変更したら正本とこのファイルの両方を更新する。

| 項目 | 値 |
|---|---|
| プロジェクト名 | local-ai-hub |
| 予約レンジ | 20800-20899 |
| ステータス | active |

## 使用中ポート

| host | container | サービス | 公開 |
|---|---|---|---|
| 20800 | 4000 | `ai-hub-gateway`(LiteLLM Proxy。推論API) | LAN |
| 20801 | 8000 | `ai-hub-chat`(動作確認チャットUI) | LAN |
| 20802 | 18789 | `ai-hub-openclaw`(OpenClaw Control UI / Gateway) | LAN |

`ai-hub-ollama`(11434)と `ai-hub-catalog`(8080)はホストへ公開しない。20803-20899 は予備。
