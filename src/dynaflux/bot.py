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
from .math_engine import WAD, compute_gross_from_net, compute_summary


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
        description="報告（受領者未指定時は自己新規発行(Mint)、指定時は通常送金(Transfer)）",
    )
    @app_commands.describe(
        amount="指定額 (DFX)",
        recipient="受領者（省略時はあなた自身への新規発行 / 指定時はその相手への送金）",
        amount_basis="額の計算基準（gross: 額面申告 / net: 手取り指定でgrossを自動逆算）",
    )
    @app_commands.choices(
        amount_basis=[
            app_commands.Choice(name="額面 (Gross) — 既定", value="gross"),
            app_commands.Choice(name="手取り (Net) — 逆算", value="net"),
        ]
    )
    async def report_cmd(
        interaction: discord.Interaction,
        amount: float,
        recipient: discord.User | None = None,
        amount_basis: str = "gross",
    ) -> None:
        now = int(time.time())
        actor_id = str(interaction.user.id)
        is_transfer = recipient is not None and str(recipient.id) != actor_id
        target_user = recipient or interaction.user
        target_id = str(target_user.id)

        try:
            if amount <= 0:
                raise ValueError("amount は 0 より大きい数値を指定してください。")

            late_state, early_state = compute_summary(
                bot.ledger.snapshot.late_state,
                bot.ledger.snapshot.early_state,
                bot.ledger.snapshot.last_timestamp,
                now,
            )

            if amount_basis == "net":
                target_net_wad = int(round(amount * WAD))
                gross_wad = compute_gross_from_net(
                    target_net_wad, late_state, early_state, is_gen=not is_transfer
                )
                gross_dfx = gross_wad / WAD
            else:
                gross_dfx = amount

            if is_transfer:
                record = bot.ledger.transfer(
                    sender=actor_id,
                    recipient=target_id,
                    gross_dfx=gross_dfx,
                    timestamp=now,
                )
                net_dfx = record.net_wad / WAD
                burned_dfx = (record.gross_wad - record.net_wad) / WAD
                basis_note = f"（手取り目標: `{amount:,.4f} DFX` から逆算）" if amount_basis == "net" else ""
                await interaction.response.send_message(
                    f"**[送金報告完了 (#{record.step})]**\n"
                    f"- 移転者: <@{actor_id}> → 受領者: <@{target_id}>\n"
                    f"- 額面 (Gross): `{gross_dfx:,.4f} DFX` {basis_note}\n"
                    f"- **税引き後手取り (Net): `{net_dfx:,.6f} DFX`** （焼却税: `{burned_dfx:,.6f} DFX`）\n"
                    f"- 状態証明 (`stateHash`): `{record.new_state_hash[:16]}...`"
                )
            else:
                record, elapsed_ms = bot.ledger.mint(
                    recipient=target_id,
                    gross_dfx=gross_dfx,
                    timestamp=now,
                )
                net_dfx = record.net_wad / WAD
                basis_note = f"（手取り目標: `{amount:,.4f} DFX` から逆算）" if amount_basis == "net" else ""
                await interaction.response.send_message(
                    f"**[発行報告完了 (#{record.step})]**\n"
                    f"- 受領者: <@{target_id}>\n"
                    f"- 申告額 (Gross): `{gross_dfx:,.4f} DFX` {basis_note}\n"
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
