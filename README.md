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

Codexログイン状態はDocker named volume `codex-state` に自動保存されます。ホスト側の `.codex` パスを `.env` に書く必要はありません。初回はDiscordで `認証` を実行してください。

Grok X Searchを使うなら:

```env
XAI_API_KEY=...
GROK_MODEL=grok-3-mini
```

Discordで対話したいならBot Tokenが必要です。BotをDiscord Developer Portalで作り、Message Content Intentを有効にします。

KabuBotはprivate botとして動きます。`.env` で指定したサーバー/チャンネル/ユーザー以外には反応せず、指定外のサーバーへ入った場合は自動で退出します。DiscordのDeveloper Modeを有効にして、サーバーID、チャンネルID、ユーザーIDをコピーしてください。
`DISCORD_ALLOWED_USER_IDS` にはDiscordユーザー名ではなく数値IDを入れます。Discordのユーザーメンション形式 `<@123...>` でも読み取れます。

```env
DISCORD_BOT_TOKEN=...
DISCORD_ALLOWED_GUILD_ID=123456789012345678
DISCORD_ALLOWED_CHANNEL_ID=123456789012345678
DISCORD_ALLOWED_USER_IDS=123456789012345678,234567890123456789
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

Discord botにも同じ自然文を投げられます。`.env` の `DISCORD_ALLOWED_CHANNEL_ID` で指定したチャンネル内で、`DISCORD_ALLOWED_USER_IDS` のユーザーからの投稿だけに反応します。DMには反応しません。

初回だけ、許可済みチャンネルでCodex認証を行います。

```text
@KabuBot 認証
```

表示されたURL/codeでCodexログインを完了したら:

```text
@KabuBot 認証完了
```

Codex認証状態はDocker named volume `codex-state` に保存されます。Discord側の許可ユーザーとcron送信先は `.env` の `DISCORD_ALLOWED_*` だけで決まります。

通常の指示例:

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

Discord botで毎朝レポートを送るには、`DISCORD_BOT_TOKEN` とprivate allowlistを設定します。cronレポートは `DISCORD_ALLOWED_CHANNEL_ID` に送られます。

```env
DISCORD_BOT_TOKEN=...
DISCORD_ALLOWED_GUILD_ID=123456789012345678
DISCORD_ALLOWED_CHANNEL_ID=123456789012345678
DISCORD_ALLOWED_USER_IDS=123456789012345678
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
