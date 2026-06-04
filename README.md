# KabuBot

Dockerで動く株価監視botです。Codex runnerを中に置き、yfinanceで株価・履歴を取り、Grok X SearchでX上の話題化や煽りを拾い、毎日市場開始時に「今アツい/怪しい急落銘柄」をサマリーします。

主な用途は、ソフトウェア株などでAI/LLMへの過剰反応らしい急落を見つけ、理由と不確実性をまとめることです。投資助言ではなく、調査の入口として使う前提です。

## 構成

- `codex-runner`: 参考元 `PsychologicalCounselor` と同じ形のCodex認証HTTP runner。
- `monitor`: yfinance、Grok X Search、ML anomaly score、Discord対話、通知、スケジューラ。
- `skills/`: Codexが分析時に参照する `SKILL.md` 群。

## 起動

```bash
cp .env.example .env
```

既存のCodexログインを使うなら:

```env
HOST_CODEX_HOME=/Users/mizuame/.codex
```

Linux VM上で新しくCodexログインするなら:

```env
HOST_CODEX_HOME=/opt/kabubot/.codex-state
```

このディレクトリはコンテナ内の `codex` ユーザーが書ける必要があります。今回のrunnerではUIDが1001なので、Linux VMでは必要に応じて:

```bash
sudo chown -R 1001:1001 /opt/kabubot/.codex-state
```

Grok X Searchを使うなら:

```env
XAI_API_KEY=...
GROK_MODEL=grok-3-mini
```

DiscordでDM/メンション対話したいならBot Tokenが必要です。BotをDiscord Developer Portalで作り、Message Content Intentを有効にしてから:

```env
DISCORD_BOT_TOKEN=...
DISCORD_OWNER_USER_ID=あなたのDiscord user id
DISCORD_REPORT_CHANNEL_ID=毎朝レポートを投げるchannel id
```

起動:

```bash
docker compose --env-file .env up -d --build
```

Codex認証確認:

```bash
curl http://127.0.0.1:8790/auth/status
```

未ログインの場合:

```bash
curl -X POST http://127.0.0.1:8790/auth/start
```

## 使い方

手動スキャン:

```bash
curl -X POST http://127.0.0.1:8790/scan \
  -H "Content-Type: application/json" \
  -d '{"sector_query":"ソフトウェア","notify":false}'
```

特定銘柄の価格シグナル:

```bash
curl http://127.0.0.1:8790/quotes/MSFT,CRM,NOW,4478.T
```

最新レポート:

```bash
curl http://127.0.0.1:8790/reports/latest.md
```

監視テーマ/個別watchを自然言語で更新:

```bash
curl -X POST http://127.0.0.1:8790/watch \
  -H "Content-Type: application/json" \
  -d '{"message":"ソフトウェアだけ。固定銘柄はいらない"}'
```

```bash
curl -X POST http://127.0.0.1:8790/watch \
  -H "Content-Type: application/json" \
  -d '{"message":"AIインフラに変えて。NVDAとAMDも候補に入れて"}'
```

```bash
curl -X POST http://127.0.0.1:8790/watch \
  -H "Content-Type: application/json" \
  -d '{"message":"MSFTとCRMだけ見て"}'
```

現在のwatch状態:

```bash
curl http://127.0.0.1:8790/watch
```

Discord botにも同じ自然文を投げられます。DM、メンション、または `!kabu` prefixに反応します。

```text
ソフトウェアだけ。固定銘柄はいらない
AIインフラに変えて。NVDAとAMDも候補に入れて
MSFTとCRMだけ見て
今のテーマで急落を探して
最新レポート
```

デフォルトでは米国市場の平日 09:32 ET に自動スキャンします。

```env
MARKET_TIMEZONE=America/New_York
MARKET_OPEN_SCAN_CRON=32 9 * * 1-5
```

日本市場向けにするなら例:

```env
MARKET_TIMEZONE=Asia/Tokyo
MARKET_OPEN_SCAN_CRON=5 9 * * 1-5
```

## 通知

Discord botで毎朝レポートを送る:

```env
DISCORD_BOT_TOKEN=...
DISCORD_REPORT_CHANNEL_ID=...
```

Discord webhookでもレポート投稿だけはできます。ただしWebhookは対話できません。

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

Slack webhook:

```env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Webhook未設定でも `data/latest.md` と `data/reports/` に保存されます。

## Proxmox VM配置メモ

今回のVM配置先は `root@100.97.114.85` 上の VM 101 `gpu-docker` です。

- 配置先: `/opt/kabubot`
- サービス: `docker compose --env-file .env up -d`
- APIはVM内の `127.0.0.1:8790` にbindしています。

Proxmoxホストから状態を見る例:

```bash
ssh root@100.97.114.85 \
  "qm guest exec 101 -- bash -lc 'cd /opt/kabubot && docker compose --env-file .env ps'"
```

VM内APIを叩く例:

```bash
ssh root@100.97.114.85 \
  "qm guest exec 101 -- bash -lc 'curl -sS http://127.0.0.1:8790/reports/latest.md'"
```

## セクター指定

`SECTOR_QUERY` や `/scan` の `sector_query` は自然言語で指定できます。

例:

- `ソフトウェア`
- `AIインフラ`
- `半導体`
- `ヘルスケア`

個別watchは `.env` に固定しません。`/watch` に自然文を投げると `data/watch.json` に保存され、次回以降の自動スキャンにも反映されます。

ソフトウェアの場合は、米国SaaS/AIソフト銘柄と一部日本のソフトウェア銘柄を初期ユニバースとして使い、yfinanceのセクター情報が取れた場合はそれで絞り込みます。個別銘柄を追加した場合は、その銘柄を候補に混ぜます。「だけ」と指示した場合はその銘柄だけを見ます。

## 参考API

- xAI X Search docs: https://docs.x.ai/developers/tools/x-search
- yfinance docs: https://ranaroussi.github.io/yfinance/
