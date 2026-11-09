# DynaFlux (DFX) — Autonomous Verifiable Thermostatic Currency

<p align="center">
  <img src="assets/bot_icon.png" alt="DynaFlux Logo" width="180" />
</p>

DynaFlux (DFX) は、2つの時間スケールの指数減衰状態（`late` と `early`）によって通貨の発行量と移転税率を自律制御する、**完全な分割不変性（Path-Independent Split-Invariance）** と **暗号学的無改竄証明（`stateHash` 監査チェーン + 軽量 Proof-of-Work）** を備えた $O(1)$ サーモスタット暗号資産プロトコルです。

**完全 0 円・サーバーレス・24 時間稼働** で、Ethereum Sepolia（または任意の EVM チェーン）上のスマートコントラクト（`contracts/DynaFluxToken.sol`）と **Cloudflare Workers** 上の Discord Bot（`cloudflare/`）を直結して運用できます。MetaMask アプリやブラウザの Remix IDE を使わずに CLI だけで鍵生成・コンパイル・デプロイが完結し、外部ウォレットへの直接出金（`/withdraw`）にも対応しています。

## 目次 <!-- omit in toc -->

- [1. 数理モデルと暗号学的性質](#1-数理モデルと暗号学的性質)
   - [1.1. 2変数サーモスタット状態 (`late` と `early`)](#11-2変数サーモスタット状態-late-と-early)
   - [1.2. 分割不変性（一気に送っても小分けにしても総課税量が不変）](#12-分割不変性一気に送っても小分けにしても総課税量が不変)
   - [1.3. 100% Pure Burn と軽量 Proof-of-Work (PoW) 発行](#13-100-pure-burn-と軽量-proof-of-work-pow-発行)
   - [1.4. 計算の無改竄証明 (`stateHash` 監査チェーン)](#14-計算の無改竄証明-statehash-監査チェーン)
- [2. ディレクトリ構成](#2-ディレクトリ構成)
- [3. ローカル検証・パラメータ調整とコントラクトへのフリーズ](#3-ローカル検証パラメータ調整とコントラクトへのフリーズ)
- [4. 完全無料・24時間稼働の本番導入ガイド（Sepolia × Cloudflare Workers）](#4-完全無料24時間稼働の本番導入ガイドsepolia--cloudflare-workers)
   - [4.1. Step 1: テストネットへのコントラクト自動デプロイ（MetaMask・Remix 不要）](#41-step-1-テストネットへのコントラクト自動デプロイmetamaskremix-不要)
   - [4.2. Step 2: Cloudflare Workers への Discord Bot デプロイ](#42-step-2-cloudflare-workers-への-discord-bot-デプロイ)
   - [4.3. Step 3: Discord スラッシュコマンドの登録と利用](#43-step-3-discord-スラッシュコマンドの登録と利用)

## 1. 数理モデルと暗号学的性質

### 1.1. 2変数サーモスタット状態 (`late` と `early`)

過去の全履歴を毎回走査する $O(N)$ 計算を廃し、経過時間 $\Delta t$ に応じた指数減衰で更新される 2 つの状態変数 `late`（直近熱量：7日で 0.1 倍に減衰）と `early`（基準熱量：28日ラグ基準値＋最低着火値 `MIN_EARLY = 1,000 DFX`）を保持します。

1. 減衰関数 `expNegWad` には区間縮小つき有理関数近似（パデ型逆数近似）を採用しており、何ヶ月取引が途絶えてもテイラー展開のようなゼロ落ち破綻や振動を起こしません。
1. 取引や新規発行が行われると、**税引き後の手取り額（`net`）のみ** が熱量状態に加算され、自律的な負のフィードバックループを形成します。

### 1.2. 分割不変性（一気に送っても小分けにしても総課税量が不変）

熱量余力に基づく限界受取率

$$\frac{d(\text{net})}{d(\text{gross})} = \frac{\max(\text{early} - c \cdot (\text{late} + \text{net}),\ 0)}{\text{early}}$$

を厳密積分した閉形式解を採用しています（通常移転時は $c = 0.90$ 、新規発行時は $c = 0.90 \times 1.8 = 1.62$ ）。

$$\text{net}(\text{gross}) = \frac{\max(\text{early} - c \cdot \text{late},\ 0)}{c} \times \left(1 - \exp\left(-\frac{c \cdot \text{gross}}{\text{early}}\right)\right)$$

1. **完全な分割不変性**:
   - 同一時刻において額面 $X$ を 1 回で送っても、 $X = x_1 + x_2 + \dots + x_n$ のように何十回に小分けにして送っても、受け取れる $\sum \text{net}_i$ と焼却される税金総額 $\sum (\text{gross}_i - \text{net}_i)$ が数学的に一致します。
1. **2段階サーモスタット上限（発行遮断と移転遮断の分離）**:
   - 直近熱量 `late` が `early` の約 61.7% （ $1 / 1.62$ ）に達すると新規発行（Mint）の手取りが `0` となって自動的に停止し、既存トークンの移転（ $c = 0.90$ ）のみが `early` の約 111.1% まで継続可能となります。

### 1.3. 100% Pure Burn と軽量 Proof-of-Work (PoW) 発行

1. **100% 焼却（特権・中央金庫なし）**:
   - 徴収された税金（ $\text{gross} - \text{net}$ ）はすべて消滅（Burn）します。オーナー権限や `stakingPool` / `treasury` による中抜きは一切存在しません。
1. **スマホ対応の超軽量理論 PoW**:
   - 新規発行時は、最新の `stateHash` に束縛された軽量 PoW パズル（先頭 8 ビットゼロ、スマホや Worker で 1 ミリ秒未満）を解くことで、時間クールダウン制限なしで誰でも匿名・平等に発行できます。

### 1.4. 計算の無改竄証明 (`stateHash` 監査チェーン)

すべての状態遷移（ステップ $n$ ）は、直前の状態ハッシュ $\text{stateHash}_{n-1}$ とすべての入出力パラメータを暗号学的ハッシュで連鎖させます。

$$\text{stateHash}_n = H\left(\text{stateHash}_{n-1} \parallel t_n \parallel \text{sender}_n \parallel \text{recipient}_n \parallel \text{gross}_n \parallel \text{net}_n \parallel \text{powNonce}_n \parallel \text{late}_n \parallel \text{early}_n\right)$$

## 2. ディレクトリ構成

```text
dynaflux/
├── assets/
│   ├── bot_icon.png                   # Discord Bot アイコン・ブランドエンブレム
│   └── pv.mp4                         # プロモーションビデオ (16:9, 10s)
├── config/
│   └── params.yml                     # トークノミクス・PoW パラメータ設定ファイル (SSOT)
├── contracts/
│   └── DynaFluxToken.sol              # EVM チェーンにデプロイする自律型 ERC-20 コントラクト
├── docs/
│   └── promo_and_profile.md           # Bot プロフィール定義 & PV 生成プロンプト
├── cloudflare/                        # 完全無料・24時間稼働の Cloudflare Workers Discord Bot
│   ├── scripts/
│   │   └── deploy_contract.ts         # MetaMask・Remix 不要の自動鍵生成・コンパイル・デプロイツール
│   ├── src/
│   │   └── worker.ts                  # Discord Ed25519 署名検証 + オンチェーン直結実行エンジン
│   ├── package.json
│   ├── tsconfig.json
│   └── wrangler.toml
├── src/
│   └── dynaflux/                      # Python 決定論的エンジン・YAML コンパイラ・CLI
│       ├── __init__.py
│       ├── config.py                  # params.yml 読み込み & Solidity 定数フリーズ機構
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

## 3. ローカル検証・パラメータ調整とコントラクトへのフリーズ

`config/params.yml` で係数（`const`, `gen_dis_rate`, 減衰日数 `decay_to_tenth_days`, `min_early_dfx`, `difficulty_bits`）を自由に編集・実験し、確定した値を `uv run dynaflux freeze` でスマートコントラクト（`contracts/DynaFluxToken.sol`）の `constant` 定数へ焼き込んで永久フリーズできます。

```sh
# 依存パッケージの同期
uv sync

# 1. config/params.yml の値でシミュレーション & 改竄検知デモを実行
uv run dynaflux simulate

# 2. 調整した config/params.yml の値を contracts/DynaFluxToken.sol に焼き込む（フリーズ）
uv run dynaflux freeze

# 3. Lint・フォーマット・型検査・単体テストの一括実行
uv run ruff check && uv run pyright && uv run pytest
```

## 4. 完全無料・24時間稼働の本番導入ガイド（Sepolia × Cloudflare Workers）

この構成では、**ブロックチェーン（Ethereum Sepolia テストネット）自体が世界中に分散複製された永久無料のデータベース**となり、**Cloudflare Workers（無料枠）が 24 時間 365 日稼働する Discord の窓口**になります。参加メンバーはスマホの Discord アプリだけで利用でき、何もインストールする必要はありません。

- **稼働中コントラクト (Ethereum Sepolia)**: [`0xa3718990f03078abc3c3673a52b4e45f48ed9604`](https://sepolia.etherscan.io/token/0xa3718990f03078abc3c3673a52b4e45f48ed9604)

### 4.1. Step 1: テストネットへのコントラクト自動デプロイ（MetaMask・Remix 不要）

MetaMask アプリやブラウザの Remix IDE を一切使わずに、コマンド 1 つで secp256k1 鍵ペア生成から `solc` ローカルコンパイル、コントラクトのデプロイ、`wrangler.toml` の更新まで全自動で完了します。

1. 初回実行でテストネット用ウォレット（アドレスと秘密鍵 `.relayer-wallet.json`）を自動作成します：

   ```sh
   cd cloudflare
   npm install
   npm run chain-deploy
   ```

1. 画面に表示された `[ウォレットアドレス]`（`0x...`）をコピーし、メインネット残高（0.001 ETH 等）が不要な以下の無料 Faucet に貼り付けて **Sepolia ETH** を受け取ります：
   - [Google Cloud Web3 Sepolia Faucet](https://cloud.google.com/application/web3/faucet/ethereum/sepolia)（Google アカウントでログインするだけ・残高制限なし）
   - [Sepolia PoW Faucet (pk910)](https://sepolia-faucet.pk910.de/)（ブラウザで数分マイニングするだけ・残高制限なし）
1. Sepolia ETH が届いたら、もう一度同じコマンドを実行します：

   ```sh
   npm run chain-deploy
   ```

   これで `contracts/DynaFluxToken.sol` が自動コンパイル・デプロイされ、`cloudflare/wrangler.toml` の `CONTRACT_ADDRESS` も自動で書き換えられます。

### 4.2. Step 2: Cloudflare Workers への Discord Bot デプロイ

1. 以下のコマンドで Cloudflare Workers（無料枠）へデプロイし、シークレットを設定します。

   ```sh
   cd cloudflare
   npx wrangler login
   npx wrangler deploy

   # Discord Developer Portal -> General Information の Public Key と Application ID
   npx wrangler secret put DISCORD_PUBLIC_KEY
   npx wrangler secret put DISCORD_APP_ID

   # Step 1 で自動生成された cloudflare/.relayer-wallet.json 内の privateKey (0x...)
   npx wrangler secret put RELAYER_PRIVATE_KEY
   ```

1. デプロイ完了時に表示される URL（`https://dynaflux-discord-worker.<サブドメイン>.workers.dev`）を、[Discord Developer Portal](https://discord.com/developers/applications) の **General Information -> Interactions Endpoint URL** に貼り付けて `Save Changes` を押します。

### 4.3. Step 3: Discord スラッシュコマンドの登録と利用

1. スラッシュコマンド（`/report`, `/withdraw`, `/balance`, `/summary`, `/verify`）を Discord に登録します（初回のみ）。

   ```sh
   DISCORD_APP_ID="あなたのAppID" DISCORD_BOT_TOKEN="あなたのBotToken" \
     uv run python scripts/register_discord_commands.py
   ```

1. これで PC の電源を切っても、Discord サーバーのメンバー全員が 24 時間いつでも以下のコマンドを使えます：
   - `/report amount:100`: **【全体公開】** 軽量 PoW を解いてオンチェーンで新規発行（Mint）し、成果を共有します（受領者未指定時は自分に発行）。
   - `/report amount:100 amount_basis:net`: **【全体公開】** 手取り額（Net）を 100 DFX に指定して新規発行します（必要な額面を自動逆算）。
   - `/report amount:20 recipient:@friend`: **【全体公開】** メンバー間でオンチェーン送金します（受領者を指定すると自動的に通常送金となり、差額の税金は 100% Burn）。
   - `/report amount:20 recipient:@friend amount_basis:net`: **【全体公開】** 相手への手取りがちょうど 20 DFX になるように自動逆算して送金します。
   - `/balance [user]`: **【本人限定】** Discord アカウントに紐づく決定論的オンチェーンアドレスと現在の DFX 残高を自分だけに表示します。
   - `/summary [is_gen]`: **【本人限定】** ブロックチェーン上の基準熱量（`early`）と直近熱量（`late`）、`gross -> net` 早見表を自分だけに表示します。
   - `/withdraw address:0x外部ウォレットアドレス amount:10`: **【本人限定】** 外部ウォレットへの出金処理を安全に行います（出金先アドレスや途中経過は本人にのみ表示）。
   - `/verify`: **【本人限定】** ブロックチェーン上の最新 `stateHash` と累積ステップ数を監査検証します。
