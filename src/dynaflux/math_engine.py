"""
Deterministic fixed-point mathematics & cryptographic primitives for DynaFlux (DFX).
Mirrors `contracts/DynaFluxToken.sol` with 100% bit-for-bit integer precision.
"""

import hashlib
import time

WAD: int = 10**18

# Original `token_manage` constants:
# const = 0.98, gen_dis_rate = 1.6 -> c_transfer = 0.98, c_gen = 0.98 * 1.6 = 1.568
C_TRANSFER: int = 980_000_000_000_000_000
C_GEN: int = 1_568_000_000_000_000_000

# Decay rates:
# `late` decays to 0.1x over 30 days (2,592,000 seconds)
LAMBDA_LATE: int = 888_342_958_043
LAMBDA_EARLY: int = 296_114_319_347

# Built-in minimum baseline to prevent deadlock after multi-month inactivity
MIN_EARLY: int = 150 * WAD

# Lightweight Theoretical Proof-of-Work (8 leading zero bits = avg 256 hashes, < 1ms on mobile)
POW_DIFFICULTY_BITS: int = 8
POW_TARGET: int = ((1 << 256) - 1) >> POW_DIFFICULTY_BITS


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
    """Returns the decayed (late, early) state in WAD at `current_timestamp`."""
    dt = max(0, current_timestamp - last_timestamp)
    d_late = exp_neg_wad(LAMBDA_LATE * dt)
    d_early = exp_neg_wad(LAMBDA_EARLY * dt)

    late = (late_state * d_late) // WAD
    matured = ((late_state - late) * d_early) // WAD if late_state > late else 0
    decayed_early = (early_state * d_early) // WAD + matured
    early = max(MIN_EARLY, decayed_early)
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
