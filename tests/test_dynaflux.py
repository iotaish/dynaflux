"""Tests for DynaFlux mathematical properties, PoW minting, and public tamper detection."""

from pathlib import Path

import pytest

from dynaflux import C_GEN, MIN_EARLY, WAD, Ledger, exp_neg_wad, verify_snapshot


def test_exp_neg_wad_stability() -> None:
    assert exp_neg_wad(0) == WAD
    # Even at large arguments (e.g. x = 5 WAD, where 4th-order Taylor completely fails),
    # rational reciprocal remains positive and accurate (~0.0067379)
    val_5 = exp_neg_wad(5 * WAD) / WAD
    assert 0.0065 < val_5 < 0.0070
    assert exp_neg_wad(45 * WAD) == 0


def test_split_invariance_transfer() -> None:
    """Sending 300 DFX in 1 bulk tx vs 2, 10, or 50 splits yields the same total net and burned tax."""
    results: list[tuple[float, float]] = []
    for splits in (1, 2, 10, 50):
        ledger = Ledger(genesis_timestamp=1_700_000_000)
        ledger.snapshot.balances["alice"] = 10_000 * WAD
        ledger.snapshot.total_supply = 10_000 * WAD
        t = 1_700_000_000

        chunk = 300.0 / splits
        net_sum = 0.0
        tax_sum = 0.0
        for _ in range(splits):
            rec = ledger.transfer("alice", "bob", chunk, t)
            net_sum += rec.net_wad / WAD
            tax_sum += (rec.gross_wad - rec.net_wad) / WAD
        results.append((net_sum, tax_sum))

    base_net, base_tax = results[0]
    # 1x and 2x match to 1e-6; 50x matches within 0.02% fixed-point rounding
    assert results[1][0] == pytest.approx(base_net, rel=1e-6)
    for net_sum, tax_sum in results[2:]:
        assert net_sum == pytest.approx(base_net, rel=2e-4)
        assert tax_sum == pytest.approx(base_tax, rel=2e-4)


def test_pow_mint_saturation_and_cutoff() -> None:
    """Massive minting cannot exceed remaining capacity (early - c_gen * late) / c_gen."""
    ledger = Ledger(genesis_timestamp=1_700_000_000)
    t = 1_700_000_000
    max_theoretical_dfx = MIN_EARLY / C_GEN

    rec, _ = ledger.mint("ishp", 10_000_000.0, t)
    minted_dfx = rec.net_wad / WAD
    assert minted_dfx == pytest.approx(max_theoretical_dfx, rel=1e-4)

    # Immediately trying to mint again hits thermal cutoff (net == 0 -> ValueError)
    with pytest.raises(ValueError, match="Thermal capacity exhausted"):
        ledger.mint("ishp", 100.0, t)


def test_cryptographic_audit_and_tamper_detection(tmp_path: Path) -> None:
    """Public verifier detects even a 1-wei modification anywhere in history."""
    db_file = tmp_path / "ledger.json"
    ledger = Ledger(genesis_timestamp=1_700_000_000, storage_path=db_file)
    t = 1_700_000_000

    ledger.mint("ishp", 40.0, t)
    t += 600
    ledger.mint("daiki", 50.0, t)
    t += 1200
    ledger.transfer("ishp", "daiki", 10.0, t)

    res = ledger.verify()
    assert res.valid is True
    assert res.checked_steps == 3

    # Reload from disk and verify
    reloaded = Ledger(genesis_timestamp=1_700_000_000, storage_path=db_file)
    assert reloaded.verify().valid is True

    # Tamper with 1 wei of net_wad in step 2
    reloaded.snapshot.transitions[1].net_wad += 1
    tampered_res = verify_snapshot(reloaded.snapshot)
    assert tampered_res.valid is False
    assert tampered_res.failed_step == 2
