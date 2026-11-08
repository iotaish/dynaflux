# DynaFlux (DFX) — Autonomous Verifiable Thermostatic Currency

DynaFlux (DFX) は、Silvio Gesell の自由貨幣論（減価・流通速度制御）と元の `token_manage` プロトコルの数理モデルを、**完全な分割不変性（Path-Independent Split-Invariance）** と **暗号学的無改竄証明（`stateHash` 監査チェーン + 軽量 Proof-of-Work）** を備えた $O(1)$ 自律型プロトコルへと進化させた実装です。

**完全 0 円・サーバーレス・24 時間稼働** で、無料テストネット（Base Sepolia 等）上のスマートコントラクト（`contracts/DynaFluxToken.sol`）と **Cloudflare Workers** 上の Discord Bot（`cloudflare/`）を直結して運用できます。もちろん MetaMask 等の外部ウォレットへの直接出金（`/withdraw`）にも対応しています。

## 目次 <!-- omit in toc -->

- [1. 数理モデルと暗号学的性質](#1-数理モデルと暗号学的性質)
   - [1.1. 2変数サーモスタット状態 (`late` と `early`)](#11-2変数サーモスタット状態-late-と-early)
   - [1.2. 分割不変性（一気に送っても小分けにしても総課税量が不変）](#12-分割不変性一気に送っても小分けにしても総課税量が不変)
   - [1.3. 100% Pure Burn と軽量 Proof-of-Work (PoW) 発行](#13-100-pure-burn-と軽量-proof-of-work-pow-発行)
   - [1.4. 計算の無改竄証明 (`stateHash` 監査チェーン)](#14-計算の無改竄証明-statehash-監査チェーン)
- [2. ディレクトリ構成](#2-ディレクトリ構成)
- [3. ローカル検証・シミュレーション](#3-ローカル検証シミュレーション)
- [4. 完全無料・24時間稼働の本番導入ガイド（Testnet × Cloudflare Workers）](#4-完全無料24時間稼働の本番導入ガイドtestnet--cloudflare-workers)
   - [4.1. Step 1: 無料テストネット（Base Sepolia）へのコントラクトデプロイ](#41-step-1-無料テストネットbase-sepoliaへのコントラクトデプロイ)
   - [4.2. Step 2: Cloudflare Workers への Discord Bot デプロイ](#42-step-2-cloudflare-workers-への-discord-bot-デプロイ)
   - [4.3. Step 3: Discord スラッシュコマンドの登録と利用](#43-step-3-discord-スラッシュコマンドの登録と利用)

## 1. 数理モデルと暗号学的性質

### 1.1. 2変数サーモスタット状態 (`late` と `early`)

過去の全履歴を毎回走査する $O(N)$ 計算を廃し、経過時間 $\Delta t$ に応じた指数減衰で更新される 2 つの状態変数 `late`（直近熱量：30日で $0.1$ 倍に減衰）と `early`（基準熱量：30日ラグ基準値＋最低着火値 `MIN_EARLY = 150 DFX`）を保持します。

1. 減衰関数 `expNegWad` には区間縮小つき有理関数近似（パデ型逆数近似）を採用しており、何ヶ月取引が途絶えてもテイラー展開のようなゼロ落ち破綻や振動を起こしません。
1. 取引や新規発行が行われると、元の `token_manage` と同じく **税引き後の手取り額（`net`）のみ** が熱量状態に加算されます。

### 1.2. 分割不変性（一気に送っても小分けにしても総課税量が不変）

元の `token_manage` における限界受取率

$$\frac{d(\text{net})}{d(\text{gross})} = \frac{\max(\text{early} - c \cdot (\text{late} + \text{net}),\ 0)}{\text{early}}$$

を厳密積分した閉形式解を採用しています（通常移転時は $c = 0.98$、新規発行時は $c = 0.98 \times 1.6 = 1.568$）。

$$\text{net}(\text{gross}) = \frac{\max(\text{early} - c \cdot \text{late},\ 0)}{c} \times \left(1 - \exp\left(-\frac{c \cdot \text{gross}}{\text{early}}\right)\right)$$

1. **完全な分割不変性**:
   - 同一時刻において額面 $X$ を 1 回で送っても、$X = x_1 + x_2 + \dots + x_n$ のように何十回に小分けして送っても、受け取れる $\sum \text{net}_i$ と焼却される税金総額 $\sum (\text{gross}_i - \text{net}_i)$ が数学的に完全一致します。
1. **過熱時の完全遮断（サーモスタット上限）**:
   - 1回で 100 億枚を申告・送金しても、手取り額は残余容量 $\frac{\text{early} - c \cdot \text{late}}{c}$ を決して超えられず、$c \cdot \text{late} \ge \text{early}$ に達した瞬間に手取りは `0`（税率 100%）となり、冷却されるまで自動的に遮断されます。

### 1.3. 100% Pure Burn と軽量 Proof-of-Work (PoW) 発行

1. **100% 焼却（特権・中央金庫なし）**:
   - 徴収された税金（$\text{gross} - \text{net}$）はすべて消滅（Burn）します。オーナー権限や `stakingPool` / `treasury` による中抜きは一切存在しません。
1. **スマホ対応の超軽量理論 PoW**:
   - 新規発行（元の `transferor == ""` 報告）時は、最新の `stateHash` に束縛された軽量 PoW パズル（先頭 8 ビットゼロ、スマホや Worker で 1 ミリ秒未満）を解くことで、誰でも匿名・平等に発行できます。

### 1.4. 計算の無改竄証明 (`stateHash` 監査チェーン)

すべての状態遷移（ステップ $n$）は、直前の状態ハッシュ $\text{stateHash}_{n-1}$ とすべての入出力パラメータを暗号学的ハッシュで連鎖させます。

$$\text{stateHash}_n = H\left(\text{stateHash}_{n-1} \parallel t_n \parallel \text{sender}_n \parallel \text{recipient}_n \parallel \text{gross}_n \parallel \text{net}_n \parallel \text{powNonce}_n \parallel \text{late}_n \parallel \text{early}_n\right)$$

## 2. ディレクトリ構成

```text
dynaflux/
├── contracts/
│   └── DynaFluxToken.sol              # Base Sepolia 等にデプロイする自律型 ERC-20 コントラクト
├── cloudflare/                        # 完全無料・24時間稼働の Cloudflare Workers Discord Bot
│   ├── src/
│   │   └── worker.ts                  # Discord Webhook 署名検証 + オンチェーン直結実行エンジン
│   ├── package.json
│   ├── tsconfig.json
│   └── wrangler.toml
├── src/
│   └── dynaflux/                      # Python 決定論的エンジン・ローカル Discord Bot・CLI
│       ├── __init__.py
│       ├── math_engine.py
│       ├── ledger.py
│       ├── bot.py
│       └── cli.py
├── scripts/
│   ├── simulate.py                    # 分割不変性・PoW・改竄検知の検証スクリプト
│   └── register_discord_commands.py   # Discord スラッシュコマンド一括登録スクリプト
├── tests/
│   └── test_dynaflux.py               # pytest 自動検証スイート
└── pyproject.toml
```

## 3. ローカル検証・シミュレーション

```sh
# 依存パッケージの同期
uv sync

# シミュレーション & 改竄検知デモの実行
uv run dynaflux simulate

# Lint・フォーマット・型検査・単体テストの一括実行
uv run ruff check && uv run pyright && uv run pytest
```

## 4. 完全無料・24時間稼働の本番導入ガイド（Testnet × Cloudflare Workers）

この構成では、**ブロックチェーン（Base Sepolia テストネット）自体が世界中に分散複製された永久無料のデータベース**となり、**Cloudflare Workers（無料枠）が 24 時間 365 日稼働する Discord の窓口**になります。参加メンバーはスマホの Discord アプリだけで利用でき、何もインストールする必要はありません。

### 4.1. Step 1: 無料テストネット（Base Sepolia）へのコントラクトデプロイ

1. MetaMask でリレイヤー用のウォレットを 1 つ用意し、[Coinbase Base Sepolia Faucet](https://portal.cdp.coinbase.com/products/faucet) 等から無料のテスト用 ETH を受け取ります。
1. ブラウザ上で [Remix IDE](https://remix.ethereum.org/) を開き、`contracts/DynaFluxToken.sol` を貼り付けてコンパイルします。
1. `Deploy & Run Transactions` タブで Environment を `Injected Provider - MetaMask`（Base Sepolia ネットワーク）にし、コンストラクタ引数 `initialRelayer` に自分のウォレットアドレスを入れて `Deploy` を押します。
1. デプロイされたコントラクトのアドレス（`0x...`）を控えます。

### 4.2. Step 2: Cloudflare Workers への Discord Bot デプロイ

1. `cloudflare/wrangler.toml` の `CONTRACT_ADDRESS` に、Step 1 でデプロイしたコントラクトアドレスを記入します。
1. 以下のコマンドで Cloudflare Workers（無料枠）へデプロイし、シークレットを設定します。

   ```sh
   cd cloudflare
   npm install
   npx wrangler deploy

   # Discord Developer Portal の General Information にある Public Key と Application ID
   npx wrangler secret put DISCORD_PUBLIC_KEY
   npx wrangler secret put DISCORD_APP_ID

   # Step 1 で使ったリレイヤーウォレットの秘密鍵 (0x...)
   npx wrangler secret put RELAYER_PRIVATE_KEY
   ```

1. デプロイ完了時に表示される URL（`https://dynaflux-discord-worker.<あなたのサブドメイン>.workers.dev`）を、[Discord Developer Portal](https://discord.com/developers/applications) の **General Information -> Interactions Endpoint URL** に貼り付けて `Save Changes` を押します。

### 4.3. Step 3: Discord スラッシュコマンドの登録と利用

1. スラッシュコマンド（`/report`, `/withdraw`, `/balance`, `/summary`, `/verify`）を Discord に登録します（最初の 1 回だけ実行すれば完了です）。

   ```sh
   DISCORD_APP_ID="あなたのAppID" DISCORD_BOT_TOKEN="あなたのBotToken" \
     uv run python scripts/register_discord_commands.py
   ```

1. これであなたの PC の電源を切っても、Discord サーバーのメンバー全員が 24 時間いつでも以下のコマンドを使えます：
   - `/report amount:50`: 軽量 PoW を解いてオンチェーンで新規発行（Mint）し、BaseScan のトランザクションリンクを返します。
   - `/report amount:20 recipient:@friend transfer_mode:True`: メンバー間でオンチェーン送金します。
   - `/withdraw address:0xあなたのMetaMaskアドレス amount:10`: Discord 内の自分の Vault アドレスから、外部の MetaMask ウォレットへ本物の ERC-20 トークン（DFX）として直接引き出します（MetaMask で `CONTRACT_ADDRESS` をインポートすれば残高が表示され、自由に送金できます）。
   - `/balance`, `/summary`, `/verify`: ブロックチェーン上の残高、熱量早見表、および `stateHash` 監査証明をリアルタイムに照会します。
