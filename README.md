# DynaFlux (DFX) — Autonomous Verifiable Thermostatic Currency

DynaFlux (DFX) は、Silvio Gesell の自由貨幣論（減価・流通速度制御）と元の `token_manage` プロトコルの数理モデルを、**完全な分割不変性（Path-Independent Split-Invariance）** と **暗号学的無改竄証明（`stateHash` 監査チェーン + 軽量 Proof-of-Work）** を備えた $O(1)$ 自律型プロトコルへと進化させた実装です。スマートコントラクト（`contracts/DynaFluxToken.sol`）および Discord Bot（`dynaflux-bot`）として即座に導入・運用できます。

## 目次 <!-- omit in toc -->

- [1. 数理モデルと暗号学的性質](#1-数理モデルと暗号学的性質)
   - [1.1. 2変数サーモスタット状態 (`late` と `early`)](#11-2変数サーモスタット状態-late-と-early)
   - [1.2. 分割不変性（一気に送っても小分けにしても総課税量が不変）](#12-分割不変性一気に送っても小分けにしても総課税量が不変)
   - [1.3. 100% Pure Burn と軽量 Proof-of-Work (PoW) 発行](#13-100-pure-burn-と軽量-proof-of-work-pow-発行)
   - [1.4. 計算の無改竄証明 (`stateHash` 監査チェーン)](#14-計算の無改竄証明-statehash-監査チェーン)
- [2. ディレクトリ構成](#2-ディレクトリ構成)
- [3. セットアップと検証コマンド](#3-セットアップと検証コマンド)
- [4. Discord Bot の導入手順](#4-discord-bot-の導入手順)

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
   - 新規発行（元の `transferor == ""` 報告）時は、最新の `stateHash` に束縛された軽量 PoW パズル（先頭 8 ビットゼロ、スマホや Bot で 1 ミリ秒未満）を解くことで、誰でも匿名・平等に発行できます。

### 1.4. 計算の無改竄証明 (`stateHash` 監査チェーン)

すべての状態遷移（ステップ $n$）は、直前の状態ハッシュ $\text{stateHash}_{n-1}$ とすべての入出力パラメータを暗号学的ハッシュで連鎖させます。

$$\text{stateHash}_n = H\left(\text{stateHash}_{n-1} \parallel t_n \parallel \text{sender}_n \parallel \text{recipient}_n \parallel \text{gross}_n \parallel \text{net}_n \parallel \text{powNonce}_n \parallel \text{late}_n \parallel \text{early}_n\right)$$

すべての演算が決定論的な 18 桁固定小数点整数（WAD）で定義されているため、過去のどのステップであっても $1\text{ wei}$（$10^{-18}\text{ DFX}$）の税ごまかしや 1 秒の減衰改竄があれば、誰でも `/verify` コマンドや CLI で即座に検知できます。

## 2. ディレクトリ構成

```text
dynaflux/
├── contracts/
│   └── DynaFluxToken.sol   # 自律型・分割不変・stateHash証明つき ERC-20 コントラクト
├── src/
│   └── dynaflux/
│       ├── __init__.py
│       ├── math_engine.py  # Solidity とビット単位で一致する固定小数点演算・PoW エンジン
│       ├── ledger.py       # ハッシュチェーン台帳と公開監査検証器 (verify_snapshot)
│       ├── bot.py          # Discord スラッシュコマンド Bot (/report, /balance, /summary, /verify)
│       └── cli.py          # シミュレーション・監査用 CLI
├── scripts/
│   └── simulate.py         # 単体実行可能な検証スクリプト
├── tests/
│   └── test_dynaflux.py    # 分割不変性・PoW飽和・1wei改竄検知の自動テスト
└── pyproject.toml          # uv パッケージ・ツール設定
```

## 3. セットアップと検証コマンド

パッケージ管理には `uv` を使用します。

```sh
# 依存パッケージの同期
uv sync

# シミュレーション & 改竄検知デモの実行
uv run dynaflux simulate

# Lint・フォーマット・型検査・単体テストの一括実行
uv run ruff check && uv run pyright && uv run pytest
```

## 4. Discord Bot の導入手順

高価な専用サーバーやポート開放は不要です。手元の PC や無料クラウド環境から以下の手順ですぐに稼働できます。

1. **Discord Bot トークンの取得**:
   - [Discord Developer Portal](https://discord.com/developers/applications) で `New Application` を作成します。
   - 左メニューの `Bot` から `Reset Token` を押してトークンをコピーします。
   - 左メニューの `OAuth2` -> `URL Generator` で `bot` と `applications.commands` にチェックを入れ、生成された URL から自分の Discord サーバーに Bot を招待します。
1. **環境変数の設定と起動**:
   - トークンを環境変数に渡して `dynaflux-bot` を起動します。

   ```sh
   export DISCORD_BOT_TOKEN="あなたのBotトークン"
   uv run dynaflux-bot
   ```

1. **利用可能なスラッシュコマンド**:
   - `/report amount:<額> recipient:<相手> transfer_mode:<True|False>`:
      - `transfer_mode=False`（デフォルト）: 軽量 PoW を自動で解いて新規発行（Mint）を報告します。
      - `transfer_mode=True`: あなたの残高から受領者へ送金します。
   - `/balance [user]`: 指定ユーザーまたは自分の DFX 残高と総流通量を開示します。
   - `/summary [is_gen]`: 現在の `early` / `late` 熱量状態と `gross -> net` の早見表を表示します。
   - `/verify`: 創世（Genesis）からの全履歴・税計算・PoW をその場でゼロから再計算し、1 wei の改竄もないことを数学的に証明します。
