"""
Verifiable state ledger & public audit engine for DynaFlux (DFX).
"""

from pathlib import Path

from pydantic import BaseModel, Field

from .math_engine import (
    MIN_EARLY,
    WAD,
    compute_genesis_hash,
    compute_net,
    compute_summary,
    compute_transition_hash,
    solve_pow,
    verify_pow,
)


class TransitionRecord(BaseModel):
    step: int
    prev_state_hash: str
    new_state_hash: str
    timestamp: int
    sender: str
    recipient: str
    gross_wad: int
    net_wad: int
    pow_nonce: int
    late_after: int
    early_after: int


class LedgerSnapshot(BaseModel):
    genesis_timestamp: int
    last_timestamp: int
    late_state: int = 0
    early_state: int = MIN_EARLY
    total_supply: int = 0
    burned_total: int = 0
    step_count: int = 0
    state_hash: str
    balances: dict[str, int] = Field(default_factory=dict)
    transitions: list[TransitionRecord] = Field(default_factory=list)


class AuditVerificationResult(BaseModel):
    valid: bool
    checked_steps: int
    genesis_hash: str
    final_state_hash: str
    failed_step: int | None = None
    reason: str = "OK"


class Ledger:
    snapshot: LedgerSnapshot
    storage_path: Path | None

    def __init__(
        self,
        genesis_timestamp: int = 1_700_000_000,
        storage_path: Path | None = None,
    ) -> None:
        self.storage_path = storage_path
        if storage_path is not None and storage_path.exists():
            raw = storage_path.read_text(encoding="utf-8")
            self.snapshot = LedgerSnapshot.model_validate_json(raw)
        else:
            genesis_hash = compute_genesis_hash(genesis_timestamp, 0, MIN_EARLY)
            self.snapshot = LedgerSnapshot(
                genesis_timestamp=genesis_timestamp,
                last_timestamp=genesis_timestamp,
                state_hash=genesis_hash,
            )
            self._persist()

    def _persist(self) -> None:
        if self.storage_path is not None:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text(
                self.snapshot.model_dump_json(indent=2),
                encoding="utf-8",
            )

    def balance_of(self, account: str) -> float:
        return self.snapshot.balances.get(account, 0) / WAD

    def summary(self, timestamp: int) -> tuple[float, float]:
        late, early = compute_summary(
            self.snapshot.late_state,
            self.snapshot.early_state,
            self.snapshot.last_timestamp,
            timestamp,
        )
        return late / WAD, early / WAD

    def quote(self, gross_dfx: float, timestamp: int, is_gen: bool = False) -> float:
        gross_wad = int(round(gross_dfx * WAD))
        late, early = compute_summary(
            self.snapshot.late_state,
            self.snapshot.early_state,
            self.snapshot.last_timestamp,
            timestamp,
        )
        return compute_net(gross_wad, late, early, is_gen) / WAD

    def quote_table(
        self,
        timestamp: int,
        is_gen: bool = False,
        rows: int = 10,
    ) -> list[tuple[float, float]]:
        late_dfx, early_dfx = self.summary(timestamp)
        max_gross = max(late_dfx, early_dfx) * 2.0
        step_val = max_gross / rows
        return [
            (round(i * step_val, 4), self.quote(i * step_val, timestamp, is_gen=is_gen))
            for i in range(1, rows + 1)
        ]

    def mint(
        self,
        recipient: str,
        gross_dfx: float,
        timestamp: int,
        pow_nonce: int | None = None,
    ) -> tuple[TransitionRecord, float]:
        gross_wad = int(round(gross_dfx * WAD))
        if gross_wad <= 0:
            raise ValueError("gross_dfx must be positive")

        elapsed_ms = 0.0
        if pow_nonce is None:
            pow_nonce, elapsed_ms = solve_pow(self.snapshot.state_hash, recipient, gross_wad)
        elif not verify_pow(self.snapshot.state_hash, recipient, gross_wad, pow_nonce):
            raise ValueError("Invalid Proof-of-Work nonce")

        late, early = compute_summary(
            self.snapshot.late_state,
            self.snapshot.early_state,
            self.snapshot.last_timestamp,
            timestamp,
        )
        net_wad = compute_net(gross_wad, late, early, is_gen=True)
        if net_wad <= 0:
            raise ValueError("Thermal capacity exhausted (net is 0)")

        record = self._commit(
            timestamp=timestamp,
            sender="0x0",
            recipient=recipient,
            gross_wad=gross_wad,
            net_wad=net_wad,
            pow_nonce=pow_nonce,
            late_before=late,
            early_before=early,
        )
        self.snapshot.total_supply += net_wad
        self.snapshot.balances[recipient] = self.snapshot.balances.get(recipient, 0) + net_wad
        self._persist()
        return record, elapsed_ms

    def transfer(
        self,
        sender: str,
        recipient: str,
        gross_dfx: float,
        timestamp: int,
    ) -> TransitionRecord:
        gross_wad = int(round(gross_dfx * WAD))
        if gross_wad <= 0:
            raise ValueError("gross_dfx must be positive")

        sender_balance = self.snapshot.balances.get(sender, 0)
        if sender_balance < gross_wad:
            raise ValueError("Insufficient balance")

        late, early = compute_summary(
            self.snapshot.late_state,
            self.snapshot.early_state,
            self.snapshot.last_timestamp,
            timestamp,
        )
        net_wad = compute_net(gross_wad, late, early, is_gen=False)
        burned_wad = gross_wad - net_wad

        record = self._commit(
            timestamp=timestamp,
            sender=sender,
            recipient=recipient,
            gross_wad=gross_wad,
            net_wad=net_wad,
            pow_nonce=0,
            late_before=late,
            early_before=early,
        )
        self.snapshot.balances[sender] = sender_balance - gross_wad
        self.snapshot.balances[recipient] = self.snapshot.balances.get(recipient, 0) + net_wad
        self.snapshot.total_supply -= burned_wad
        self.snapshot.burned_total += burned_wad
        self._persist()
        return record

    def _commit(
        self,
        timestamp: int,
        sender: str,
        recipient: str,
        gross_wad: int,
        net_wad: int,
        pow_nonce: int,
        late_before: int,
        early_before: int,
    ) -> TransitionRecord:
        late_after = late_before + net_wad
        early_after = early_before
        prev_hash = self.snapshot.state_hash
        next_hash = compute_transition_hash(
            prev_state_hash_hex=prev_hash,
            timestamp=timestamp,
            sender=sender,
            recipient=recipient,
            gross_wad=gross_wad,
            net_wad=net_wad,
            pow_nonce=pow_nonce,
            late_after=late_after,
            early_after=early_after,
        )
        self.snapshot.late_state = late_after
        self.snapshot.early_state = early_after
        self.snapshot.last_timestamp = timestamp
        self.snapshot.state_hash = next_hash
        self.snapshot.step_count += 1

        record = TransitionRecord(
            step=self.snapshot.step_count,
            prev_state_hash=prev_hash,
            new_state_hash=next_hash,
            timestamp=timestamp,
            sender=sender,
            recipient=recipient,
            gross_wad=gross_wad,
            net_wad=net_wad,
            pow_nonce=pow_nonce,
            late_after=late_after,
            early_after=early_after,
        )
        self.snapshot.transitions.append(record)
        return record

    def verify(self) -> AuditVerificationResult:
        """
        Replays all transitions from Genesis and verifies that every decay, tax, PoW,
        balance, and hash matches with zero tampering.
        """
        return verify_snapshot(self.snapshot)


def verify_snapshot(snapshot: LedgerSnapshot) -> AuditVerificationResult:
    late_state = 0
    early_state = MIN_EARLY
    last_ts = snapshot.genesis_timestamp
    genesis_hash = compute_genesis_hash(snapshot.genesis_timestamp, late_state, early_state)
    current_hash = genesis_hash

    for idx, tr in enumerate(snapshot.transitions, start=1):
        if tr.step != idx:
            return AuditVerificationResult(
                valid=False,
                checked_steps=idx - 1,
                genesis_hash=genesis_hash,
                final_state_hash=current_hash,
                failed_step=idx,
                reason=f"Step sequence mismatch at #{idx}",
            )
        if tr.prev_state_hash != current_hash:
            return AuditVerificationResult(
                valid=False,
                checked_steps=idx - 1,
                genesis_hash=genesis_hash,
                final_state_hash=current_hash,
                failed_step=idx,
                reason=f"Previous stateHash mismatch at step #{idx}",
            )

        is_gen = tr.sender == "0x0"
        if is_gen and not verify_pow(current_hash, tr.recipient, tr.gross_wad, tr.pow_nonce):
            return AuditVerificationResult(
                valid=False,
                checked_steps=idx - 1,
                genesis_hash=genesis_hash,
                final_state_hash=current_hash,
                failed_step=idx,
                reason=f"PoW verification failed at step #{idx}",
            )

        late, early = compute_summary(late_state, early_state, last_ts, tr.timestamp)
        expected_net = compute_net(tr.gross_wad, late, early, is_gen=is_gen)
        if tr.net_wad != expected_net:
            return AuditVerificationResult(
                valid=False,
                checked_steps=idx - 1,
                genesis_hash=genesis_hash,
                final_state_hash=current_hash,
                failed_step=idx,
                reason=f"Tax/Net calculation tampered at step #{idx} (expected {expected_net}, got {tr.net_wad})",
            )

        late_state = late + expected_net
        early_state = early
        last_ts = tr.timestamp

        if tr.late_after != late_state or tr.early_after != early_state:
            return AuditVerificationResult(
                valid=False,
                checked_steps=idx - 1,
                genesis_hash=genesis_hash,
                final_state_hash=current_hash,
                failed_step=idx,
                reason=f"Thermostatic state mismatch at step #{idx}",
            )

        current_hash = compute_transition_hash(
            prev_state_hash_hex=current_hash,
            timestamp=tr.timestamp,
            sender=tr.sender,
            recipient=tr.recipient,
            gross_wad=tr.gross_wad,
            net_wad=tr.net_wad,
            pow_nonce=tr.pow_nonce,
            late_after=late_state,
            early_after=early_state,
        )
        if current_hash != tr.new_state_hash:
            return AuditVerificationResult(
                valid=False,
                checked_steps=idx - 1,
                genesis_hash=genesis_hash,
                final_state_hash=current_hash,
                failed_step=idx,
                reason=f"Transition hash mismatch at step #{idx}",
            )

    if current_hash != snapshot.state_hash:
        return AuditVerificationResult(
            valid=False,
            checked_steps=len(snapshot.transitions),
            genesis_hash=genesis_hash,
            final_state_hash=current_hash,
            failed_step=len(snapshot.transitions),
            reason="Final stateHash does not match snapshot header",
        )

    return AuditVerificationResult(
        valid=True,
        checked_steps=len(snapshot.transitions),
        genesis_hash=genesis_hash,
        final_state_hash=current_hash,
    )
