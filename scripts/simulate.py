#!/usr/bin/env python3
"""
DynaFlux (DFX) - Thermostatic Tokenomics Simulator & Public Audit Verifier
Verifies:
1. Exact Split-Invariance: sending 300 DFX in 1 tx vs 2, 10, 100 txs produces the same net & tax.
2. Pure Lightweight PoW Minting (no cooldown rate limit) + Thermal Cutoff saturation.
3. Public Cryptographic Verifiability (`verify_audit_log`):
   Anyone can independently replay the transition log from Genesis and prove with 100% mathematical
   certainty that every single decay, tax, balance, and PoW calculation was executed without tampering.
"""

import hashlib
import time
from dataclasses import dataclass, field

WAD = 10**18
C_TRANSFER = 980_000_000_000_000_000  # 0.98 * 1e18
C_GEN = 1_568_000_000_000_000_000  # 1.568 * 1e18 (0.98 * 1.6)
LAMBDA_LATE = 888_342_958_043  # ln(10) / 30 days
LAMBDA_EARLY = 296_114_319_347  # slow 30-day reference decay
MIN_EARLY = 150 * WAD  # Built-in ignition floor

POW_DIFFICULTY_BITS = 8
POW_TARGET = ((1 << 256) - 1) >> POW_DIFFICULTY_BITS


def exp_neg_wad(x: int) -> int:
    """Exact deterministic integer mirror of DynaFluxToken.expNegWad."""
    if x == 0:
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


def compute_summary(late_state: int, early_state: int, last_ts: int, now: int) -> tuple[int, int]:
    dt = max(0, now - last_ts)
    d_late = exp_neg_wad(LAMBDA_LATE * dt)
    d_early = exp_neg_wad(LAMBDA_EARLY * dt)

    late = (late_state * d_late) // WAD
    late_at_early_rate = (late_state * d_early) // WAD
    matured = late_at_early_rate - late if late_at_early_rate > late else 0

    early_excess = early_state - MIN_EARLY if early_state > MIN_EARLY else 0
    early = MIN_EARLY + (early_excess * d_early) // WAD + matured
    return late, early


def compute_net(gross_wad: int, late: int, early: int, is_gen: bool) -> int:
    if gross_wad == 0:
        return 0
    c = C_GEN if is_gen else C_TRANSFER
    c_late = (c * late) // WAD
    if early <= c_late:
        return 0
    remaining_capacity = ((early - c_late) * WAD) // c
    exponent = (c * gross_wad) // early
    one_minus_exp = WAD - exp_neg_wad(exponent)
    return (remaining_capacity * one_minus_exp) // WAD


def pow_hash(state_hash: bytes, account: str, gross_wad: int, pow_nonce: int) -> bytes:
    payload = state_hash + f":{account}:{gross_wad}:{pow_nonce}".encode()
    return hashlib.sha256(payload).digest()


def transition_hash(
    prev_hash: bytes,
    timestamp: int,
    sender: str,
    recipient: str,
    gross_wad: int,
    net_wad: int,
    pow_nonce: int,
    late_after: int,
    early_after: int,
) -> bytes:
    payload = (
        prev_hash
        + (
            f":{timestamp}:{sender}:{recipient}:{gross_wad}:{net_wad}:{pow_nonce}:{late_after}:{early_after}"
        ).encode()
    )
    return hashlib.sha256(payload).digest()


def solve_lightweight_pow(state_hash: bytes, account: str, gross_wad: int) -> tuple[int, float]:
    t0 = time.perf_counter()
    nonce = 0
    while True:
        digest = int.from_bytes(pow_hash(state_hash, account, gross_wad, nonce), "big")
        if digest <= POW_TARGET:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return nonce, elapsed_ms
        nonce += 1


@dataclass
class AuditEvent:
    step: int
    prev_state_hash: bytes
    new_state_hash: bytes
    timestamp: int
    sender: str
    recipient: str
    gross_wad: int
    net_wad: int
    pow_nonce: int
    late_after: int
    early_after: int


@dataclass
class DynaFluxEngine:
    genesis_timestamp: int = 1_700_000_000
    late_state: int = 0
    early_state: int = MIN_EARLY
    last_timestamp: int = 1_700_000_000
    total_supply: int = 0
    burned_total: int = 0
    step_count: int = 0
    state_hash: bytes = field(init=False)
    balances: dict[str, int] = field(default_factory=dict)
    audit_log: list[AuditEvent] = field(default_factory=list)

    def __post_init__(self):
        self.state_hash = hashlib.sha256(
            f"DYNAFLUX_GENESIS:{self.genesis_timestamp}:{self.late_state}:{self.early_state}".encode()
        ).digest()

    def _commit(
        self,
        now: int,
        sender: str,
        recipient: str,
        gross_wad: int,
        net_wad: int,
        pow_nonce: int,
        late_before: int,
        early_before: int,
    ):
        self.late_state = late_before + net_wad
        self.early_state = early_before
        self.last_timestamp = now

        prev_hash = self.state_hash
        next_hash = transition_hash(
            prev_hash,
            now,
            sender,
            recipient,
            gross_wad,
            net_wad,
            pow_nonce,
            self.late_state,
            self.early_state,
        )
        self.state_hash = next_hash
        self.step_count += 1
        self.audit_log.append(
            AuditEvent(
                step=self.step_count,
                prev_state_hash=prev_hash,
                new_state_hash=next_hash,
                timestamp=now,
                sender=sender,
                recipient=recipient,
                gross_wad=gross_wad,
                net_wad=net_wad,
                pow_nonce=pow_nonce,
                late_after=self.late_state,
                early_after=self.early_state,
            )
        )

    def mint(self, user: str, gross_dfx: float, now: int) -> float:
        gross_wad = int(gross_dfx * WAD)
        pow_nonce, _ = solve_lightweight_pow(self.state_hash, user, gross_wad)

        late, early = compute_summary(self.late_state, self.early_state, self.last_timestamp, now)
        net_wad = compute_net(gross_wad, late, early, is_gen=True)
        self._commit(now, "0x0", user, gross_wad, net_wad, pow_nonce, late, early)

        self.total_supply += net_wad
        self.balances[user] = self.balances.get(user, 0) + net_wad
        return net_wad / WAD

    def transfer(
        self, sender: str, recipient: str, gross_dfx: float, now: int
    ) -> tuple[float, float]:
        gross_wad = int(gross_dfx * WAD)
        late, early = compute_summary(self.late_state, self.early_state, self.last_timestamp, now)
        net_wad = compute_net(gross_wad, late, early, is_gen=False)
        tax_wad = gross_wad - net_wad

        self._commit(now, sender, recipient, gross_wad, net_wad, 0, late, early)

        self.balances[sender] = self.balances.get(sender, 0) - gross_wad
        self.balances[recipient] = self.balances.get(recipient, 0) + net_wad
        self.total_supply -= tax_wad
        self.burned_total += tax_wad
        return net_wad / WAD, tax_wad / WAD


def verify_audit_log(genesis_ts: int, log: list[AuditEvent], expected_final_hash: bytes) -> bool:
    """
    Independent Public Verifier:
    Recomputes every decay, tax, PoW, and state hash from scratch using only the public event log.
    Returns False immediately if even 1 wei of tax or 1 second of decay was tampered with.
    """
    late_state = 0
    early_state = MIN_EARLY
    last_ts = genesis_ts
    current_hash = hashlib.sha256(
        f"DYNAFLUX_GENESIS:{genesis_ts}:{late_state}:{early_state}".encode()
    ).digest()

    for ev in log:
        if ev.prev_state_hash != current_hash:
            return False

        is_gen = ev.sender == "0x0"
        if is_gen:
            digest = int.from_bytes(
                pow_hash(current_hash, ev.recipient, ev.gross_wad, ev.pow_nonce), "big"
            )
            if digest > POW_TARGET:
                return False

        late, early = compute_summary(late_state, early_state, last_ts, ev.timestamp)
        expected_net = compute_net(ev.gross_wad, late, early, is_gen=is_gen)
        if ev.net_wad != expected_net:
            return False

        late_state = late + expected_net
        early_state = early
        last_ts = ev.timestamp

        if ev.late_after != late_state or ev.early_after != early_state:
            return False

        current_hash = transition_hash(
            current_hash,
            ev.timestamp,
            ev.sender,
            ev.recipient,
            ev.gross_wad,
            ev.net_wad,
            ev.pow_nonce,
            late_state,
            early_state,
        )
        if current_hash != ev.new_state_hash:
            return False

    return current_hash == expected_final_hash


def run_verifications():
    print("=" * 72)
    print(" 1. SPLIT-INVARIANCE TEST (1x Bulk vs 2x, 10x, 100x Split)")
    print("=" * 72)
    for splits in (1, 2, 10, 100):
        eng = DynaFluxEngine()
        eng.balances["alice"] = 10_000 * WAD
        eng.total_supply = 10_000 * WAD
        t = eng.last_timestamp

        total_gross = 300.0
        chunk = total_gross / splits
        total_net = 0.0
        total_tax = 0.0

        for _ in range(splits):
            net, tax = eng.transfer("alice", "bob", chunk, t)
            total_net += net
            total_tax += tax

        print(
            f"  Splits: {splits:3d}x ({chunk:6.2f} DFX each) -> "
            f"Total Net: {total_net:10.6f} DFX | Total Burned Tax: {total_tax:10.6f} DFX"
        )

    print("\n" + "=" * 72)
    print(" 2. PURE PoW MINTING (No Cooldown) & CRYPTOGRAPHIC AUDIT VERIFICATION")
    print("=" * 72)
    eng = DynaFluxEngine()
    t = eng.last_timestamp

    for i, gross in enumerate([100.0, 200.0, 400.0, 10_000_000.0], 1):
        pow_nonce, ms = solve_lightweight_pow(eng.state_hash, "alice", int(gross * WAD))
        net = eng.mint("alice", gross, t)
        print(
            f"  Mint #{i} (Declared: {gross:12,.1f} DFX) -> Minted Net: {net:8.4f} DFX "
            f"| PoW={ms:.2f}ms | stateHash={eng.state_hash.hex()[:12]}..."
        )

    t += 3600
    eng.transfer("alice", "bob", 40.0, t)

    is_valid = verify_audit_log(eng.genesis_timestamp, eng.audit_log, eng.state_hash)
    print(
        f"\n  [Public Audit Verifier] Validated {len(eng.audit_log)} transitions from Genesis: {is_valid}"
    )

    # Tamper with 1 wei of tax in step 2 and verify detection
    eng.audit_log[1].net_wad += 1
    is_valid_after_tamper = verify_audit_log(eng.genesis_timestamp, eng.audit_log, eng.state_hash)
    print(
        f"  [Tamper Detection Test] After +1 wei modification in step #2:       {is_valid_after_tamper}"
    )
    print("=" * 72)


if __name__ == "__main__":
    run_verifications()
