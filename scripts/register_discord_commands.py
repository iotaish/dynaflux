#!/usr/bin/env python3
"""
Registers DynaFlux Discord slash commands (/report, /withdraw, /balance, /summary, /verify)
via the Discord HTTP API so the Cloudflare Worker can handle them serverless 24/7.

Usage:
  DISCORD_APP_ID=... DISCORD_BOT_TOKEN=... uv run python scripts/register_discord_commands.py
"""

import json
import os
import urllib.request

COMMANDS = [
    {
        "name": "report",
        "description": "オンチェーン報告（軽量PoWによる新規発行、またはメンバー間の送金）",
        "options": [
            {
                "name": "amount",
                "description": "申告額 (gross DFX)",
                "type": 10,  # NUMBER (float)
                "required": True,
            },
            {
                "name": "recipient",
                "description": "受領者（省略時は自分自身）",
                "type": 6,  # USER
                "required": False,
            },
            {
                "name": "transfer_mode",
                "description": "True: 残高から送金 / False: 軽量PoWを解いて新規発行 (Mint)",
                "type": 5,  # BOOLEAN
                "required": False,
            },
        ],
    },
    {
        "name": "withdraw",
        "description": "Discord内のDFX残高を外部のMetaMaskウォレットアドレス (0x...) へ直接引き出します",
        "options": [
            {
                "name": "address",
                "description": "送り先のMetaMask / EVMアドレス (0x...)",
                "type": 3,  # STRING
                "required": True,
            },
            {
                "name": "amount",
                "description": "引き出す額面 (gross DFX)",
                "type": 10,  # NUMBER
                "required": True,
            },
        ],
    },
    {
        "name": "balance",
        "description": "オンチェーン資産開示（DFX残高とブロックチェーン上のアドレスを確認）",
        "options": [
            {
                "name": "user",
                "description": "確認するメンバー（省略時は自分）",
                "type": 6,  # USER
                "required": False,
            }
        ],
    },
    {
        "name": "summary",
        "description": "オンチェーン取得額サマリー（現在の early / late 熱量と gross -> net 早見表）",
        "options": [
            {
                "name": "is_gen",
                "description": "True: 新規発行(Mint)の早見表 / False: 通常移転の早見表",
                "type": 5,  # BOOLEAN
                "required": False,
            }
        ],
    },
    {
        "name": "verify",
        "description": "ブロックチェーン上の最新 stateHash と累積ステップ数を監査検証します",
    },
]


def register_commands() -> None:
    app_id = os.getenv("DISCORD_APP_ID")
    bot_token = os.getenv("DISCORD_BOT_TOKEN")
    if not app_id or not bot_token:
        raise RuntimeError("Set DISCORD_APP_ID and DISCORD_BOT_TOKEN environment variables first.")

    url = f"https://discord.com/api/v10/applications/{app_id}/commands"
    data = json.dumps(COMMANDS).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="PUT",
        headers={
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
            "User-Agent": "DynaFluxBot (https://github.com/iotaish/dynaflux, 0.9.1)",
        },
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        print(f"Successfully registered {len(body)} global slash commands:")
        for cmd in body:
            print(f"  - /{cmd['name']}: {cmd['description']}")


if __name__ == "__main__":
    register_commands()
