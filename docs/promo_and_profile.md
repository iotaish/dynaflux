# DynaFlux (DFX) — Bot Profile & Promotional Video Script (Google Omni / Veo)

## 目次 <!-- omit in toc -->

- [1. Discord Bot プロフィール設定一式](#1-discord-bot-プロフィール設定一式)
   - [1.1. アイコン (Avatar)](#11-アイコン-avatar)
   - [1.2. 表示名・ステータスメッセージ・About Me](#12-表示名ステータスメッセージabout-me)
- [2. Google Omni / Veo 向けプロモーションビデオ生成スクリプト (60秒構成)](#2-google-omni--veo-向けプロモーションビデオ生成スクリプト-60秒構成)
   - [2.1. 一括投入用マスタープロンプト (Google Omni / Vids 用)](#21-一括投入用マスタープロンプト-google-omni--vids-用)
   - [2.2. シーン別カット割り & 映像生成プロンプト + ナレーション台本](#22-シーン別カット割り--映像生成プロンプト--ナレーション台本)

## 1. Discord Bot プロフィール設定一式

### 1.1. アイコン (Avatar)

- ファイルパス: `assets/bot_icon.png` (1024×1024 PNG)
- モチーフ:
   - 左のシアンブルーのリング: 長期基準熱量 **`early`**（28日ラグ記憶・冷却基準）
   - 右のアンバーゴールド〜クリムゾンのリング: 直近熱量 **`late`**（7日減衰・加熱速度）
   - 中央の六角形コア: 改竄不可能な **`stateHash`** 暗号学的コミットメント

### 1.2. 表示名・ステータスメッセージ・About Me

- **Name**: `DynaFlux`
- **About Me / Description (190文字以内・Discord 適用済み)**:

   ```text
   Autonomous Verifiable Thermostatic Currency (DFX) on Ethereum Sepolia.
   • 2-State Thermostat (early & late) + 100% Pure Burn
   • Exact Split-Invariance & Lightweight PoW Minting
   • Contract: 0x5518...be21 | GitHub: github.com/iotaish/dynaflux
   ```

- **日本語版紹介文（サーバー固定メッセージ・告知チャンネル用）**:

   ```text
   【DynaFlux (DFX) — 自律型サーモスタット暗号資産】
   2つの減衰熱量状態（early / late）によって、通貨の発行上限と移転税率を O(1) で自律制御するオンチェーンプロトコルです。
   ・分割不変性: 一括で送っても小分けで送っても総課税額・受取額が不変
   ・100% Pure Burn: オーナー特権や中抜き金庫なし、徴収税は100%自動焼却
   ・完全検証可能: すべての遷移を stateHash チェーンと Ethereum Sepolia 上に記録

   コマンド:
   /summary — 現在の early / late 熱量と gross -> net 早見表
   /report  — 軽量PoWによる新規発行(Mint) または メンバー間送金
   /balance — オンチェーン残高と専用Vaultアドレスの確認
   /withdraw — 外部ウォレット(MetaMask等)への直接出金
   /verify  — 最新の stateHash と稼働ステータスの監査証明
   ```

## 2. Google Omni / Veo 向けプロモーションビデオ生成スクリプト (60秒構成)

### 2.1. 一括投入用マスタープロンプト (Google Omni / Vids 用)

以下のブロックをそのまま Google Omni に貼り付けることで、全体の構成・ビジュアルトーン・ナレーション・テロップを一括生成できます。

```text
Create a sleek, high-tech 60-second promotional video for "DynaFlux (DFX)", an autonomous verifiable thermostatic cryptocurrency protocol running on Ethereum Sepolia and operated via a serverless Discord Bot.

[Visual Style & Aesthetic]
- Deep obsidian dark mode background (#0B0E14) with luminous cyan-blue (#00F0FF, representing the long-term baseline "early") and warm amber-crimson (#FF6B00, representing short-term transaction heat "late").
- Clean mathematical typography, glassmorphic UI cards, glowing hexagonal cryptographic hash chains, and smooth 60fps motion graphics.
- Music: Minimalist futuristic synthwave with a steady, rhythmic pulse that accelerates during the "Overheat" scene and resolves into a calm, resonant chord.

[Story Structure (60s)]
1. 00:00-00:10 | The Problem: Static tokenomics fail. Speculative bot rushes and whale dumps destabilize digital economies, while fixed taxes punish everyday users.
2. 00:10-00:22 | The Core Innovation (Dual-State Thermostat): Introduce DynaFlux (DFX). Two continuous exponential decay states—`early` (baseline memory) and `late` (current heat)—regulate minting and transfer burn in O(1) time.
3. 00:22-00:35 | Exact Split-Invariance & 100% Pure Burn: Show the closed-form integral formula `net(gross) = ((early - c * late) / c) * (1 - exp(-c * gross / early))`. Whether sending 1,000 DFX in one bulk transfer or splitting it into 100 micro-transfers, total tax burned is mathematically identical. 100% of tax is permanently burned to 0x0.
4. 00:35-00:48 | Zero-Friction Discord Experience: Show a mobile Discord chat executing `/summary`, `/report`, and `/verify`. Powered by Cloudflare Workers and lightweight <1ms Proof-of-Work, every command settles directly on Ethereum Sepolia without requiring users to install wallets—while supporting direct `/withdraw` to MetaMask anytime.
5. 00:48-01:00 | Call to Action & Outro: Reveal the interlocking cyan-and-gold DynaFlux emblem with the live contract address `0x5518963068663df44026d589800218dd4647be21` and `github.com/iotaish/dynaflux`. Tagline: "DynaFlux — Self-Stabilizing. Split-Invariant. Cryptographically Verifiable."
```

### 2.2. シーン別カット割り & 映像生成プロンプト + ナレーション台本

各シーンを個別に動画生成（Veo 等）する場合のプロンプトとナレーション・字幕です。

1. **Scene 1 (00:00 - 00:10): 既存トークンの課題（過熱と崩壊）**
   - **Video Prompt**:
      `Cinematic 3D motion graphics on a dark obsidian background. A fragile crystalline ledger shatters under a sudden surge of chaotic red laser beams representing high-frequency bot spam and whale dumping. Camera pulls back smoothly, shallow depth of field, 4k, 60fps.`
   - **Narration (JP)**:
      「従来の暗号資産は、投機ボットの急襲による暴走か、一律課税による日常決済の停滞という二択を強いられてきました。」
   - **On-Screen Text**:
      `Static Tokenomics Break Under Speculative Surges.`

1. **Scene 2 (00:10 - 00:22): 2変数サーモスタット (`early` & `late`)**
   - **Video Prompt**:
      `Two luminous interlocking energy rings floating in dark space: a steady cyan-blue outer ring ("early" baseline) and a pulsing amber-gold inner ring ("late" velocity). As the amber ring flares up, the cyan ring smoothly constricts a glowing aperture at the center, regulating the flow of golden particles into equilibrium. Futuristic HUD telemetry overlay.`
   - **Narration (JP)**:
      「DynaFlux は、長期基準『early』と直近熱量『late』の2つの指数減衰状態だけで、通貨の発行と焼却税率を O(1) で自律制御します。」
   - **On-Screen Text**:
      `O(1) Dual-State Thermostat: early (28d Baseline) × late (7d Velocity)`

1. **Scene 3 (00:22 - 00:35): 完全な分割不変性と 100% Pure Burn**
   - **Video Prompt**:
      `Split-screen mathematical visualization on dark glass. On the left, a single large golden sphere passes through a translucent prism. On the right, 100 tiny golden droplets pass through an identical prism in rapid succession. Both sides emerge with the exact same volume of cyan light while the excess dissipates into stardust (100% Burn). Floating differential equation in crisp white typography.`
   - **Narration (JP)**:
      「厳密積分された課税曲線により、一括で送っても100回に小分けしても、手取り額と焼却税は完全一致。管理者の特権や中抜きは一切なく、税金は100%自動焼却されます。」
   - **On-Screen Text**:
      `Exact Split-Invariance. Zero Admin Keys. 100% Pure Burn.`

1. **Scene 4 (00:35 - 00:48): Discord × Cloudflare × Sepolia オンチェーン直結**
   - **Video Prompt**:
      `Close-up of a sleek smartphone screen in a dark room displaying a Discord slash command "/report amount:100" and "/summary". A glowing thread of light travels instantly from the phone through an edge cloud node into an immutable chain of glowing hexagonal Ethereum blocks, returning a verified Etherscan receipt in real time.`
   - **Narration (JP)**:
      「導入は Discord からコマンドを打つだけ。Cloudflare Workers と1ミリ秒未満の軽量 PoW を介し、すべての取引が Ethereum ブロックチェーン上に刻まれ、いつでも外部ウォレットへ引き出せます。」
   - **On-Screen Text**:
      `24/7 Serverless Discord Bot → Direct On-Chain Execution & /withdraw`

1. **Scene 5 (00:48 - 01:00): エンディング & ロゴ**
   - **Video Prompt**:
      `The cyan and amber-gold Mobius rings lock into a sharp metallic hexagon emblem at the center of the frame—the DynaFlux (DFX) logo. Subtle volumetric light rays, clean white title text below: "DynaFlux (DFX) — Autonomous Verifiable Thermostatic Currency" and "github.com/iotaish/dynaflux".`
   - **Narration (JP)**:
      「すべての計算が検証可能で、自ら均衡を保つ新しい通貨プロトコル。DynaFlux。」
   - **On-Screen Text**:
      `DynaFlux (DFX) | github.com/iotaish/dynaflux`
