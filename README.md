# KabuBot

Dockerで動く株価監視botです。Codex runnerを中に置き、yfinanceで株価・履歴を取り、Grok X SearchでX上の話題化や煽りを拾い、毎日市場開始時に「今アツい/怪しい急落銘柄」をサマリーします。

主な用途は、ソフトウェア株などでAI/LLMへの過剰反応らしい急落を見つけ、理由と不確実性をまとめることです。投資助言ではなく、調査の入口として使う前提です。

## 構成

- `codex-runner`: 参考元 `PsychologicalCounselor` と同じ形のCodex認証HTTP runner。
- `monitor`: yfinance、Grok X Search、CPU統計・異常度スコア、Discord対話、通知、スケジューラ。
- `skills/`: Codexが分析時に参照する `SKILL.md` 群。

## 起動

```bash
cp .env.example .env
```

Codexログイン状態はDocker named volume `codex-state` に自動保存されます。ホスト側の `.codex` パスを `.env` に書く必要はありません。初回はDiscordで `認証` を実行してください。認証状態は保存ファイルの有無だけでなく、保存された認証でCodex上流への問い合わせまで成功した場合にのみ `authenticated` になります。トークンはCodexの通常の更新処理に任せ、状態確認のたびに強制更新しません。期限切れ時は古い認証を消してデバイス認証をやり直します。

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

slash command候補に出すには、invite URLのscopeに `applications.commands` も必要です。

```text
https://discord.com/oauth2/authorize?client_id=1511951091087048765&permissions=68608&integration_type=0&scope=bot%20applications.commands
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

## GitHub Container Registryから起動

`main`へのpush時にテスト後、CPU版の`linux/amd64`・`linux/arm64`イメージをGitHub Actionsでビルド・発行します。

- `ghcr.io/mizuamedesu/kabubot-monitor:latest`
- `ghcr.io/mizuamedesu/kabubot-runner:latest`

コミットを固定する場合は`KABUBOT_IMAGE_TAG=sha-<40桁のコミットSHA>`を指定します。配布用の`compose.registry.yml`にはローカルビルドやソースのbind mountが不要で、分析用skillsはrunnerイメージに同梱しています。

```bash
docker compose --env-file .env -f compose.registry.yml pull
docker compose --env-file .env -f compose.registry.yml up -d
```

GHCRパッケージが非公開の場合は、事前に`read:packages`権限のあるアカウントで`docker login ghcr.io`してください。

## HeteroCloudへの配置

2026-10-03にFlashへ配置済みです。適用した設定は`deploy/heterocloud/runner.json`と`deploy/heterocloud/monitor.json`に保存しています。イメージはdigestで固定しています。monitorは`ec151c920a57244e83991e05d50ff4749b4b35c6`、runnerは認証確認を修正した`11ad87a2184f3696cab65f08151b3127a88a7193`のビルドです。

- Bot本体: `01a0ff77-c342-7a80-b596-85bb3c792c89`（1 vCPU / 1536 MiB / ディスク5 GiB）
- Codex runner: `01a0ff76-0c51-7752-b251-792c215ff47d`（0.5 vCPU / 768 MiB / ディスク5 GiB）
- どちらも内部公開、常時1レプリカ。DiscordへはBotから接続します。
- Flashの永続ホーム配下の`/root/kabubot-data`にwatch・レポート・通知履歴、`/root/kabubot-codex`にCodex認証を保存します。サービス削除時は事前にバックアップしてください。
- Discord Bot TokenとxAI API KeyはFlashのシークレット管理へ登録し、`secret_env`で参照します。JSONにはトークンを保存していません。
- cronはBot内のAPSchedulerで実行します。株価スキャンは`32 9 * * mon-fri`、決算・配当チェックは`0 7 * * *`、タイムゾーンは`America/New_York`です。日本時間ではそれぞれ夏時間22:32 / 20:00、冬時間23:32 / 21:00になります。

初回のCodexログインは許可済みDiscordチャンネルで`/auth action:start`を実行し、表示されたURLで認証後、`/auth action:finish`で確認します。未認証でも株価取得・cron・イベント通知は動き、株価レポートの文章はフォールバックを使います。

稼働状態の確認:

```bash
heterocloud flash get 01a0ff77-c342-7a80-b596-85bb3c792c89
heterocloud flash get 01a0ff76-0c51-7752-b251-792c215ff47d
```

設定変更後の再適用（`project_id`は作成時のみ使用）:

```bash
jq 'del(.project_id)' deploy/heterocloud/runner.json | heterocloud flash update 01a0ff76-0c51-7752-b251-792c215ff47d -f -
jq 'del(.project_id)' deploy/heterocloud/monitor.json | heterocloud flash update 01a0ff77-c342-7a80-b596-85bb3c792c89 -f -
```

Flashコンソールのシェルから`curl -fsS http://127.0.0.1:8790/health`でcronの起動状態、`/config`で設定を確認できます。Botの起動ログは`/tmp/kabubot-monitor.log`です。

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

Discord botはslash commandで操作します。`.env` の `DISCORD_ALLOWED_CHANNEL_ID` で指定したチャンネル内で、`DISCORD_ALLOWED_USER_IDS` のユーザーからのコマンドだけに反応します。DMや普通の雑談には反応しません。

初回だけ、許可済みチャンネルでCodex認証を行います。

```text
@KabuBot 認証
```

表示されたURL/codeでCodexログインを完了したら:

```text
@KabuBot 認証完了
```

Codex認証状態はDocker named volume `codex-state` に保存されます。Discord側の許可ユーザーとcron送信先は `.env` の `DISCORD_ALLOWED_*` だけで決まります。

Discordコマンド例:

```text
/help
/watch action:show
/watch action:set text:ソフトウェアだけ。固定銘柄はいらない
/scan
/scan sector:ソフトウェア
/quote symbols:MSFT CRM NOW
/report
/chat message:いま何を見てる？
```

デフォルトでは米国市場の平日 09:32 ET に自動スキャンします。

```env
MARKET_TIMEZONE=America/New_York
MARKET_OPEN_SCAN_CRON=32 9 * * mon-fri
```

曜日は`mon-fri`のように名前で指定します。APSchedulerは月曜を`0`とするため、通常のcronで使う`1-5`では火曜〜土曜になってしまいます。

日本市場向けにするなら例:

```env
MARKET_TIMEZONE=Asia/Tokyo
MARKET_OPEN_SCAN_CRON=5 9 * * mon-fri
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

## CPUでの実行と画像の数値

GPU、CUDA、NVIDIA runtimeは不要です。NumPy / scikit-learn / Matplotlibで分析・描画します。イベント取得・カレンダー・株価画像にCodex認証は不要です。関連銘柄の文章分析は通常スキャンと同じGrok X Search・Codexを利用し、利用不可の場合は取得済みの価格指標による要約と取得状況を送ります。

`/scan` と `/quote` の画像には、1・5・20営業日の騰落率、出来高の過去20日平均に対する倍率、60日高値からの下落率、PER・予想PER・PSR、配当落ち補正後の騰落率を表示します。三角マーカーは、配当を加算した日次リターンが直前20営業日の平均から3標準偏差以上離れた日です。灰色帯は価格の直前20日平均±2標準偏差です。履歴不足や分散ゼロの場合はZ値を出しません。

比較対象は同じ取得日・元通貨・業種（欠損時はセクター）の取得済み銘柄で、自分自身を除いた日次騰落率の中央値と、その差（percentage points）、対象件数を出します。市場全体のリターンや為替調整後のリターンではありません。CPU統計スコアはスキャン対象内の相対順位に基づく調査優先度で、確率ではありません。X由来の加点は画像のCPU統計スコアには混ぜません。

## 決算・配当の事前通知と7日間のカレンダー

起動時と毎日07:00（`MARKET_TIMEZONE`、土日も含む）にイベントを取得し、**当日から6日後までの7日間**を送信します。この範囲の予定に対し、`EVENT_ALERT_DAYS`（標準7・3・1日）の直近の通知段階を表示します。停止中や遅い日程公開で閾値を過ぎた場合も、イベント前であれば次の取得時に追いついて通知します。日数は暦日です。

- テーマ監視では、個別watchに加えてテーマ銘柄とYahooセクター一覧の銘柄を自動取得します。価格ランキングや`MAX_CANDIDATES`の上位だけには絞りません。
- 対象地域は標準で米国・日本（`EVENT_REGIONS=us,jp`）。他地域を追加でき、空文字なら地域制限なし。個別watchと既知のテーマ銘柄は地域フィルタ外でも含めます。
- ソフトウェアはSoftware—Application / Software—Infrastructure、半導体は対応する2業種をページング取得します。AIはYahooの正式セクターでないため、既知のテーマ銘柄を使います。
- 「MSFTとCRMだけ」のような`symbol_mode=only`では、その個別銘柄のみ対象です。
- `EVENT_MAX_SYMBOLS`はセクター検索の取得上限（標準2000）。個別watchと既知銘柄は上限によらず含めます。上限到達・部分取得は警告に記録します。

```env
EVENT_SCAN_CRON=0 7 * * *
EVENT_ALERT_DAYS=7,3,1
EVENT_MAX_SYMBOLS=2000
EVENT_REGIONS=us,jp
```

Discordの **`/calendar`** と自動通知で、7日間のカレンダーに加えて関連銘柄の分析文と3ヶ月の株価推移画像を送ります。企業名はYahooの正式名（未提供の場合は取得できた名称）を省略せず、ティッカーを併記します。予定が多い週はカレンダーを複数画像に分けます。関連銘柄は10銘柄ずつ分析し、11銘柄目以降や価格変動が小さい銘柄も対象です。社名・価格・画像の未取得は明記します。Slack Incoming Webhookはテキスト通知のみです。

```bash
curl http://127.0.0.1:8790/events
curl http://127.0.0.1:8790/calendar.png -o calendar.png
# 当日キャッシュを更新。notify=trueを指定すると通知も実行
curl -X POST 'http://127.0.0.1:8790/events/refresh?notify=false'
```

画像には「決算予定」「権利落ち日」「配当支払日」を略さず表示します。月またぎの決算予定期間も7日間に含まれる各日に表示します。事前通知は期間の開始日を基準にし、期間が進行中の銘柄も分析対象です。提供元の市場日付を維持し、時刻や未公表日を推測しません。Yahooで取得できない日程（特に日本株）、未公表日、取得失敗は「予定なし」と区別し、画像に警告件数、`/events`に詳細を出します。決算・配当予定の完全な網羅を保証するものではありません。

`data/events/latest.json`に取得結果・表示期間・画像ファイル一覧、`calendar.png`（2ページ目以降は`calendar-2.png`等）に7日間の画像、`sent.json`に送信履歴を保存します。APIの`/calendar.png`は最初のページです。カレンダーと分析の各組を送信成功後に個別記録するので、再起動後も通常の二重通知を防ぎ、分析の途中失敗は未送信分から再試行できます。複数送信先の途中失敗や送信後・記録前のプロセス停止では再送される場合があります。日付変更は別イベントとして再通知します。月内の過去イベントは履歴として保持しますが、画像には表示しません。分析レポートは`data/events/analysis/reports/`に保存し、通常の最新スキャンを上書きしません。

日程フィールドとページング仕様は[yfinanceのカレンダー実装](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/quote.py)・[screen API](https://ranaroussi.github.io/yfinance/reference/api/yfinance.screen.html)に基づいています。

## セクター指定

`SECTOR_QUERY` や `/scan` の `sector_query` は自然言語で指定できます。

例:

- `ソフトウェア`
- `AIインフラ`
- `半導体`
- `ヘルスケア`

個別watchは `.env` に固定しません。`/watch` に自然文を投げると `data/watch.json` に保存され、次回以降の自動スキャンにも反映されます。

ソフトウェアの場合は、米国SaaS/AIソフト銘柄と一部日本のソフトウェア銘柄を初期ユニバースとして使い、yfinanceのセクター情報が取れた場合はそれで絞り込みます。個別銘柄を追加した場合は、その銘柄を候補に混ぜます。「だけ」と指示した場合はその銘柄だけを見ます。

個別watchから削除する場合は `/watch action:remove text:CRM` のように指定します。10銘柄を超えるチャートは、Discordの1メッセージ10添付制限に合わせて10枚ずつ分割送信されます。

会社名・略称による `/watch action:remove text:フィグマ` や、直近の追加まとまりを指す `/watch action:remove text:さっき追加したやつ` にも対応します。削除時の曖昧解決は現在の個別watch内だけを候補にします。テーマは `/watch action:set text:ソフトウェア系を監視` や `/watch action:set text:テーマはAIで売られているSaaS` のような自然文でも登録できます。

価格スキャンは未調整終値とyfinanceの配当イベントを使い、当日が権利落ち日なら1株配当、前日終値に対する理論下落率、配当落ち分を戻した日次騰落率をシグナルへ含めます。チャート上の `Ex-div` マーカーが権利落ち日です。

## 参考API

- xAI X Search docs: https://docs.x.ai/developers/tools/x-search
- yfinance docs: https://ranaroussi.github.io/yfinance/
