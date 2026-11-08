"""
Command-line interface for DynaFlux (DFX) simulation, local ledger operation, and public verification.
"""

import time
from pathlib import Path

import typer

from .ledger import Ledger
from .math_engine import WAD

app = typer.Typer(help="DynaFlux (DFX) - Autonomous Verifiable Thermostatic Currency CLI")


@app.command()
def simulate() -> None:
    """Run the split-invariance, PoW minting, and tamper-detection verification suite."""
    print("=" * 72)
    print(" 1. SPLIT-INVARIANCE TEST (1x Bulk vs 2x, 10x, 100x Split)")
    print("=" * 72)
    for splits in (1, 2, 10, 100):
        ledger = Ledger(genesis_timestamp=1_700_000_000)
        ledger.snapshot.balances["alice"] = 10_000 * WAD
        ledger.snapshot.total_supply = 10_000 * WAD
        t = ledger.snapshot.last_timestamp

        total_gross = 300.0
        chunk = total_gross / splits
        total_net = 0.0
        total_tax = 0.0

        for _ in range(splits):
            rec = ledger.transfer("alice", "bob", chunk, t)
            net = rec.net_wad / WAD
            tax = (rec.gross_wad - rec.net_wad) / WAD
            total_net += net
            total_tax += tax

        print(
            f"  Splits: {splits:3d}x ({chunk:6.2f} DFX each) -> "
            f"Total Net: {total_net:10.6f} DFX | Total Burned Tax: {total_tax:10.6f} DFX"
        )

    print("\n" + "=" * 72)
    print(" 2. PURE PoW MINTING & CRYPTOGRAPHIC AUDIT VERIFICATION")
    print("=" * 72)
    ledger = Ledger(genesis_timestamp=1_700_000_000)
    t = ledger.snapshot.last_timestamp

    for i, gross in enumerate([30.0, 50.0, 100.0, 10_000_000.0], 1):
        rec, ms = ledger.mint("ishp", gross, t)
        print(
            f"  Mint #{i} (Declared: {gross:12,.1f} DFX) -> Minted Net: {rec.net_wad / WAD:8.4f} DFX "
            f"| PoW={ms:.2f}ms | stateHash={rec.new_state_hash[:12]}..."
        )

    t += 3600
    ledger.transfer("ishp", "daiki", 40.0, t)

    audit = ledger.verify()
    print(f"\n  [Public Audit Verifier] Validated {audit.checked_steps} transitions: {audit.valid}")
    print("=" * 72)


@app.command()
def verify(path: Path = Path("data/ledger.json")) -> None:
    """Verify a persisted ledger JSON file from Genesis to its latest stateHash."""
    ledger = Ledger(genesis_timestamp=int(time.time()), storage_path=path)
    res = ledger.verify()
    typer.echo(
        f"Valid: {res.valid} | Steps: {res.checked_steps} | "
        f"StateHash: {res.final_state_hash} | Reason: {res.reason}"
    )
    if not res.valid:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
