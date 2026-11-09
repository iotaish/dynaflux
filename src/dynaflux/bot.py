"""
Discord Slash-Command Bot for DynaFlux (DFX).
Provides thermostatic token operations (/report, /balance, /summary)
and instant public cryptographic audit (/verify).
"""

import os
import time
from pathlib import Path

import discord
from discord import app_commands

from .ledger import Ledger
from .math_engine import WAD


class DynaFluxBot(discord.Client):
    ledger: Ledger
    tree: app_commands.CommandTree

    def __init__(self, ledger: Ledger) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.ledger = ledger
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync()


def build_bot(ledger_path: Path | None = None) -> DynaFluxBot:
    resolved_path = ledger_path or Path(os.getenv("DYNAFLUX_LEDGER_PATH", "data/ledger.json"))
    ledger = Ledger(genesis_timestamp=int(time.time()), storage_path=resolved_path)
    bot = DynaFluxBot(ledger=ledger)

    @bot.tree.command(
        name="report",
        description="報告（送金または軽量PoWによる新規発行）。sender未指定時は自己申告発行(Mint)になります。",
    )
    @app_commands.describe(
        amount="申告額 (gross DFX)",
        recipient="受領者（空欄の場合はあなた自身）",
        transfer_mode="True: あなたの残高から受領者へ送金 / False: 軽量PoWを解いて新規発行 (Mint)",
    )
    async def report_cmd(
        interaction: discord.Interaction,
        amount: float,
        recipient: discord.User | None = None,
        transfer_mode: bool = False,
    ) -> None:
        now = int(time.time())
        target_user = recipient or interaction.user
        target_id = str(target_user.id)
        actor_id = str(interaction.user.id)

        try:
            if transfer_mode:
                record = bot.ledger.transfer(
                    sender=actor_id,
                    recipient=target_id,
                    gross_dfx=amount,
                    timestamp=now,
                )
                net_dfx = record.net_wad / WAD
                burned_dfx = (record.gross_wad - record.net_wad) / WAD
                await interaction.response.send_message(
                    f"**[送金報告完了 (#{record.step})]**\n"
                    f"- 移転者: <@{actor_id}> → 受領者: <@{target_id}>\n"
                    f"- 額面 (Gross): `{amount:,.4f} DFX`\n"
                    f"- **税引き後手取り (Net): `{net_dfx:,.6f} DFX`** （焼却税: `{burned_dfx:,.6f} DFX`）\n"
                    f"- 状態証明 (`stateHash`): `{record.new_state_hash[:16]}...`"
                )
            else:
                record, elapsed_ms = bot.ledger.mint(
                    recipient=target_id,
                    gross_dfx=amount,
                    timestamp=now,
                )
                net_dfx = record.net_wad / WAD
                await interaction.response.send_message(
                    f"**[発行報告完了 (#{record.step})]**\n"
                    f"- 受領者: <@{target_id}>\n"
                    f"- 申告額 (Gross): `{amount:,.4f} DFX`\n"
                    f"- **税引き後発行額 (Net): `{net_dfx:,.6f} DFX`**\n"
                    f"- 軽量PoW検証: `nonce={record.pow_nonce}` (`{elapsed_ms:.2f}ms`)\n"
                    f"- 状態証明 (`stateHash`): `{record.new_state_hash[:16]}...`"
                )
        except ValueError as exc:
            await interaction.response.send_message(f"エラー: {exc}", ephemeral=True)

    @bot.tree.command(
        name="balance", description="資産開示（指定ユーザーまたは自分のDFX残高を確認）"
    )
    @app_commands.describe(user="確認するユーザー（省略時は自分）")
    async def balance_cmd(
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        target = user or interaction.user
        bal = bot.ledger.balance_of(str(target.id))
        supply = bot.ledger.snapshot.total_supply / WAD
        await interaction.response.send_message(
            f"**[資産開示]** <@{target.id}> の残高: **`{bal:,.6f} DFX`** （総流通量: `{supply:,.4f} DFX`）",
            ephemeral=True,
        )

    @bot.tree.command(
        name="summary",
        description="取得額サマリー（現在の early / late 熱量状態と gross -> net 早見表）",
    )
    @app_commands.describe(is_gen="True: 新規発行(Mint)時の早見表 / False: 通常送金時の早見表")
    async def summary_cmd(
        interaction: discord.Interaction,
        is_gen: bool = False,
    ) -> None:
        now = int(time.time())
        late_dfx, early_dfx = bot.ledger.summary(now)
        table = bot.ledger.quote_table(now, is_gen=is_gen, rows=8)
        mode_label = "新規発行 (c=1.62)" if is_gen else "通常移転 (c=0.90)"
        table_lines = "\n".join(f"{g:10.4f} -> {n:10.6f}" for g, n in table)
        await interaction.response.send_message(
            f"**[取得額サマリー ({mode_label})]**\n"
            f"- 基準熱量 (`early`): `{early_dfx:,.4f} DFX`\n"
            f"- 直近熱量 (`late`):  `{late_dfx:,.4f} DFX`\n"
            f"```text\n"
            f"     gross ->        net\n"
            f"{table_lines}\n"
            f"```",
            ephemeral=True,
        )

    @bot.tree.command(
        name="verify",
        description="創世(Genesis)からの全取引・減衰・税計算・PoWを再計算し、改竄がないことを数学的に証明します",
    )
    async def verify_cmd(interaction: discord.Interaction) -> None:
        res = bot.ledger.verify()
        status_icon = "VALID (改竄なし・完全一致)" if res.valid else f"INVALID ({res.reason})"
        await interaction.response.send_message(
            f"**[暗号学的監査レポート]**\n"
            f"- 判定結果: **{status_icon}**\n"
            f"- 検証済みステップ数: `{res.checked_steps}`\n"
            f"- Genesis Hash: `{res.genesis_hash[:20]}...`\n"
            f"- Latest StateHash: `{res.final_state_hash[:20]}...`",
            ephemeral=True,
        )

    return bot


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN environment variable is required. "
            "Set it in your environment or .env before starting dynaflux-bot."
        )
    bot = build_bot()
    bot.run(token)


if __name__ == "__main__":
    main()
