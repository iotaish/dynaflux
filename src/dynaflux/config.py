"""
YAML parameter loader & WAD fixed-point compiler for DynaFlux (DFX).
Allows tuning parameters in `config/params.yml` and freezing them into `contracts/DynaFluxToken.sol`.
"""

import math
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

WAD: int = 10**18
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "params.yml"
DEFAULT_CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "DynaFluxToken.sol"


class TokenMetaConfig(BaseModel):
    name: str = "DynaFlux"
    symbol: str = "DFX"


class ThermostatConfig(BaseModel):
    const: float = Field(default=0.90, gt=0.0)
    gen_dis_rate: float = Field(default=1.8, gt=0.0)
    decay_to_tenth_days: float = Field(default=7.0, gt=0.0)
    early_lag_multiplier: float = Field(default=4.0, gt=0.0)
    min_early_dfx: float = Field(default=1000.0, gt=0.0)


class PowConfig(BaseModel):
    difficulty_bits: int = Field(default=8, ge=1, le=32)


class DynaFluxParams(BaseModel):
    token: TokenMetaConfig = Field(default_factory=TokenMetaConfig)
    thermostat: ThermostatConfig = Field(default_factory=ThermostatConfig)
    pow: PowConfig = Field(default_factory=PowConfig)

    def c_transfer_wad(self) -> int:
        return int(round(self.thermostat.const * WAD))

    def c_gen_wad(self) -> int:
        return int(round(self.thermostat.const * self.thermostat.gen_dis_rate * WAD))

    def lambda_late_wad(self) -> int:
        seconds = self.thermostat.decay_to_tenth_days * 86400.0
        return int(round((math.log(10.0) / seconds) * WAD))

    def lambda_early_wad(self) -> int:
        return int(round(self.lambda_late_wad() / self.thermostat.early_lag_multiplier))

    def min_early_wad(self) -> int:
        return int(round(self.thermostat.min_early_dfx * WAD))

    def pow_target(self) -> int:
        return ((1 << 256) - 1) >> self.pow.difficulty_bits


def load_params(path: Path | None = None) -> DynaFluxParams:
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        return DynaFluxParams()
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    return DynaFluxParams.model_validate(raw or {})


def _format_solidity_uint(val: int) -> str:
    return f"{val:_}"


def freeze_params_to_solidity(
    params: DynaFluxParams,
    contract_path: Path | None = None,
) -> Path:
    """
    Rewrites the frozen constant/immutable defaults in `contracts/DynaFluxToken.sol`
    to match the exact WAD integers compiled from `config/params.yml`.
    """
    target = contract_path or DEFAULT_CONTRACT_PATH
    source = target.read_text(encoding="utf-8")

    replacements = [
        (
            r'string public constant name = "[^"]+";',
            f'string public constant name = "{params.token.name}";',
        ),
        (
            r'string public constant symbol = "[^"]+";',
            f'string public constant symbol = "{params.token.symbol}";',
        ),
        (
            r"uint256 public constant C_TRANSFER = [0-9_]+;",
            f"uint256 public constant C_TRANSFER = {_format_solidity_uint(params.c_transfer_wad())};",
        ),
        (
            r"uint256 public constant C_GEN\s*= [0-9_]+;",
            f"uint256 public constant C_GEN      = {_format_solidity_uint(params.c_gen_wad())};",
        ),
        (
            r"uint256 public constant LAMBDA_LATE\s*= [0-9_]+;",
            f"uint256 public constant LAMBDA_LATE  = {_format_solidity_uint(params.lambda_late_wad())};",
        ),
        (
            r"uint256 public constant LAMBDA_EARLY\s*= [0-9_]+;",
            f"uint256 public constant LAMBDA_EARLY = {_format_solidity_uint(params.lambda_early_wad())};",
        ),
        (
            r"uint256 public constant MIN_EARLY = [0-9_]+(?: \* WAD)?;",
            f"uint256 public constant MIN_EARLY = {_format_solidity_uint(params.min_early_wad())};",
        ),
        (
            r"uint8 public constant POW_DIFFICULTY_BITS = [0-9_]+;",
            f"uint8 public constant POW_DIFFICULTY_BITS = {params.pow.difficulty_bits};",
        ),
    ]

    updated = source
    for pattern, repl in replacements:
        updated, count = re.subn(pattern, repl, updated, count=1)
        if count == 0:
            raise ValueError(f"Pattern not found in {target}: {pattern}")

    target.write_text(updated, encoding="utf-8")
    return target
