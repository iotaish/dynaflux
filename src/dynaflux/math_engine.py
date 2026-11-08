"""
Deterministic fixed-point mathematics & cryptographic primitives for DynaFlux (DFX).
Mirrors `contracts/DynaFluxToken.sol` with 100% bit-for-bit integer precision.
Values default to `config/params.yml` (Single Source of Truth).
"""

import hashlib
import time

from .config import load_params

WAD: int = 10**18

_PARAMS = load_params()

# Thermostat coefficients loaded from `config/params.yml`:
C_TRANSFER: int = _PARAMS.c_transfer_wad()
C_GEN: int = _PARAMS.c_gen_wad()

# Decay rates loaded from `config/params.yml`:
LAMBDA_LATE: int = _PARAMS.lambda_late_wad()
LAMBDA_EARLY: int = _PARAMS.lambda_early_wad()

# Built-in minimum baseline to prevent deadlock after multi-month inactivity
MIN_EARLY: int = _PARAMS.min_early_wad()

# Lightweight Theoretical Proof-of-Work
POW_DIFFICULTY_BITS: int = _PARAMS.pow.difficulty_bits
POW_TARGET: int = _PARAMS.pow_target()


def exp_neg_wad(x: int) -> int:
    """
    Computes exp(-x) in WAD precision using range reduction and a rational reciprocal.
    Never underflows prematurely and remains strictly positive and smooth for all x < 42 WAD.
    """
    if x <= 0:
        return WAD
    if x >= 42 * WAD:
        return 0

    k = 0
    r = x
    while r >= WAD:
        r >>= 1
        k += 1

    r2 = (r * r) // WAD
    r3 = (r2 * r) // WAD
    r4 = (r3 * r) // WAD
    r5 = (r4 * r) // WAD

    exp_pos_r = WAD + r + (r2 // 2) + (r3 // 6) + (r4 // 24) + (r5 // 120)
    res = (WAD * WAD) // exp_pos_r

    for _ in range(k):
        res = (res * res) // WAD
    return res


def compute_summary(
    late_state: int,
    early_state: int,
    last_timestamp: int,
    current_timestamp: int,
) -> tuple[int, int]:
    """
    Returns the decayed (late, early) state in WAD at `current_timestamp`.
    Satisfies exact time-step associativity (semigroup property over dt):
      L(dt) = L_0 * d_late(dt)
      E(dt) = MIN_EARLY + (E_0 - MIN_EARLY) * d_early(dt) + L_0 * (d_early(dt) - d_late(dt))
    """
    dt = max(0, current_timestamp - last_timestamp)
    d_late = exp_neg_wad(LAMBDA_LATE * dt)
    d_early = exp_neg_wad(LAMBDA_EARLY * dt)

    late = (late_state * d_late) // WAD
    late_at_early_rate = (late_state * d_early) // WAD
    matured = late_at_early_rate - late if late_at_early_rate > late else 0

    early_excess = early_state - MIN_EARLY if early_state > MIN_EARLY else 0
    early = MIN_EARLY + (early_excess * d_early) // WAD + matured
    return late, early


def compute_net(gross_wad: int, late: int, early: int, is_gen: bool) -> int:
    """
    Split-invariant calculation of `net` (post-tax amount) from `gross` amount.
    Solves d(net)/d(gross) = max(early - c * (late + net), 0) / early.
    """
    if gross_wad <= 0:
        return 0

    c = C_GEN if is_gen else C_TRANSFER
    c_late = (c * late) // WAD
    if early <= c_late:
        return 0

    remaining_capacity = ((early - c_late) * WAD) // c
    exponent = (c * gross_wad) // early
    one_minus_exp = WAD - exp_neg_wad(exponent)
    return (remaining_capacity * one_minus_exp) // WAD


def compute_genesis_hash(genesis_timestamp: int, late_state: int, early_state: int) -> str:
    payload = f"DYNAFLUX_GENESIS:{genesis_timestamp}:{late_state}:{early_state}".encode()
    return hashlib.sha256(payload).hexdigest()


def compute_pow_hash(state_hash_hex: str, account: str, gross_wad: int, pow_nonce: int) -> str:
    payload = f"{state_hash_hex}:{account}:{gross_wad}:{pow_nonce}".encode()
    return hashlib.sha256(payload).hexdigest()


def verify_pow(state_hash_hex: str, account: str, gross_wad: int, pow_nonce: int) -> bool:
    digest_hex = compute_pow_hash(state_hash_hex, account, gross_wad, pow_nonce)
    return int(digest_hex, 16) <= POW_TARGET


def solve_pow(state_hash_hex: str, account: str, gross_wad: int) -> tuple[int, float]:
    """Solves the lightweight PoW puzzle and returns (nonce, elapsed_ms)."""
    t0 = time.perf_counter()
    nonce = 0
    while True:
        if verify_pow(state_hash_hex, account, gross_wad, nonce):
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return nonce, elapsed_ms
        nonce += 1


def compute_transition_hash(
    prev_state_hash_hex: str,
    timestamp: int,
    sender: str,
    recipient: str,
    gross_wad: int,
    net_wad: int,
    pow_nonce: int,
    late_after: int,
    early_after: int,
) -> str:
    payload = (
        f"{prev_state_hash_hex}:{timestamp}:{sender}:{recipient}:"
        f"{gross_wad}:{net_wad}:{pow_nonce}:{late_after}:{early_after}"
    ).encode()
    return hashlib.sha256(payload).hexdigest()
