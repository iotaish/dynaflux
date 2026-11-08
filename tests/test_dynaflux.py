"""Tests for DynaFlux mathematical properties, PoW minting, and public tamper detection."""

from pathlib import Path

import pytest

from dynaflux import (
    C_GEN,
    MIN_EARLY,
    WAD,
    DynaFluxParams,
    Ledger,
    compute_summary,
    exp_neg_wad,
    freeze_params_to_solidity,
    load_params,
    verify_snapshot,
)


def test_exp_neg_wad_stability() -> None:
    assert exp_neg_wad(0) == WAD
    # Even at large arguments (e.g. x = 5 WAD, where 4th-order Taylor completely fails),
    # rational reciprocal remains positive and accurate (~0.0067379)
    val_5 = exp_neg_wad(5 * WAD) / WAD
    assert 0.0065 < val_5 < 0.0070
    assert exp_neg_wad(45 * WAD) == 0


def test_time_step_associativity() -> None:
    """
    Evaluating decay over dt = dt_1 + ... + dt_k in one shot vs multiple intermediate steps
    yields the same (late, early) state (semigroup / time-associativity property).
    """
    t0 = 1_700_000_000
    total_dt = 7 * 86400  # 7 days
    l0 = 1_000 * WAD
    e0 = 2_000 * WAD

    late_one_shot, early_one_shot = compute_summary(l0, e0, t0, t0 + total_dt)

    # 2-step split: 3 days + 4 days
    l1, e1 = compute_summary(l0, e0, t0, t0 + 3 * 86400)
    late_two_step, early_two_step = compute_summary(l1, e1, t0 + 3 * 86400, t0 + total_dt)
    assert late_two_step / WAD == pytest.approx(late_one_shot / WAD, rel=1e-3)
    assert early_two_step / WAD == pytest.approx(early_one_shot / WAD, rel=1e-3)

    # 7-step daily split (including when starting from MIN_EARLY floor)
    late_cur, early_cur = l0, MIN_EARLY
    cur_t = t0
    for _ in range(7):
        late_cur, early_cur = compute_summary(late_cur, early_cur, cur_t, cur_t + 86400)
        cur_t += 86400
    late_floor_one_shot, early_floor_one_shot = compute_summary(l0, MIN_EARLY, t0, t0 + total_dt)
    assert late_cur / WAD == pytest.approx(late_floor_one_shot / WAD, rel=2e-3)
    assert early_cur / WAD == pytest.approx(early_floor_one_shot / WAD, rel=2e-3)


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
    for net_sum, tax_sum in results[1:]:
        assert net_sum == pytest.approx(base_net, rel=2e-6)
        assert tax_sum == pytest.approx(base_tax, rel=2e-5)


def test_pow_mint_saturation_and_cutoff() -> None:
    """Massive minting cannot exceed remaining capacity (early - c_gen * late) / c_gen."""
    ledger = Ledger(genesis_timestamp=1_700_000_000)
    t = 1_700_000_000
    max_theoretical_dfx = MIN_EARLY / C_GEN

    rec, _ = ledger.mint("alice", 10_000_000.0, t)
    minted_dfx = rec.net_wad / WAD
    assert minted_dfx == pytest.approx(max_theoretical_dfx, rel=1e-4)

    # Immediately trying to mint again hits thermal cutoff (net == 0 -> ValueError)
    with pytest.raises(ValueError, match="Thermal capacity exhausted"):
        ledger.mint("alice", 100.0, t)


def test_cryptographic_audit_and_tamper_detection(tmp_path: Path) -> None:
    """Public verifier detects even a 1-wei modification anywhere in history."""
    db_file = tmp_path / "ledger.json"
    ledger = Ledger(genesis_timestamp=1_700_000_000, storage_path=db_file)
    t = 1_700_000_000

    ledger.mint("alice", 40.0, t)
    t += 600
    ledger.mint("bob", 50.0, t)
    t += 1200
    ledger.transfer("alice", "bob", 10.0, t)

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


def test_yaml_params_and_solidity_freeze(tmp_path: Path) -> None:
    """Parameters loaded from YAML compile to WAD and freeze cleanly into Solidity source."""
    params = load_params()
    assert params.c_transfer_wad() == 900_000_000_000_000_000
    assert params.c_gen_wad() == 1_620_000_000_000_000_000

    sol_copy = tmp_path / "DynaFluxToken.sol"
    orig_sol = Path(__file__).resolve().parents[1] / "contracts" / "DynaFluxToken.sol"
    sol_copy.write_text(orig_sol.read_text(encoding="utf-8"), encoding="utf-8")

    custom = DynaFluxParams.model_validate(
        {
            "token": {"name": "CustomFlux", "symbol": "CFX"},
            "thermostat": {
                "const": 0.95,
                "gen_dis_rate": 2.0,
                "decay_to_tenth_days": 14.0,
                "early_lag_multiplier": 4.0,
                "min_early_dfx": 250.0,
            },
            "pow": {"difficulty_bits": 10},
        }
    )
    freeze_params_to_solidity(custom, sol_copy)
    frozen_text = sol_copy.read_text(encoding="utf-8")
    assert 'string public constant name = "CustomFlux";' in frozen_text
    assert "uint256 public constant C_TRANSFER = 950_000_000_000_000_000;" in frozen_text
    assert "uint8 public constant POW_DIFFICULTY_BITS = 10;" in frozen_text
