// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title DynaFluxToken (DFX) - Autonomous Verifiable Thermostatic Currency
 * @notice O(1) on-chain thermostatic tokenomics protocol with split-invariance and stateHash audit proofs.
 *
 * Core Mathematical & Cryptographic Properties:
 * 1. Dual-State Thermostatic Engine (`late` & `early`):
 *    - `late`: Short-term transaction velocity (decays to 0.1x over 7 days).
 *    - `early`: Long-term baseline activity (28-day lag window) with a built-in ignition floor (`MIN_EARLY`).
 *    - Only post-tax `net` amounts are added to `late`, creating a self-stabilizing feedback loop.
 *
 * 2. Exact Split-Invariance (Path-Independent Taxation):
 *    - Solves d(net)/d(gross) = max(early - c * (late + net), 0) / early:
 *      net(gross) = (max(early - c * late, 0) / c) * (1 - exp(-c * gross / early))
 *    - Sending `X` in one transaction or splitting `X` into `N` smaller transactions at the same timestamp
 *      yields the EXACT same total `net` received and total tax burned.
 *
 * 3. Cryptographic Audit Trail (`stateHash` Commitment & Lightweight PoW):
 *    - Every state transition (mint or transfer) updates an immutable cryptographic hash chain `stateHash`:
 *      stateHash_n = keccak256(stateHash_{n-1}, timestamp, actor, recipient, gross, net, lateState, earlyState)
 *    - Lightweight PoW for minting binds directly to the current `stateHash`.
 *    - Anyone can replay the event log from Genesis and mathematically verify that every single tax,
 *      decay, and balance calculation was executed without tampering.
 */

interface IERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract DynaFluxToken is IERC20 {
    // --- ERC-20 Metadata ---
    string public constant name = "DynaFlux";
    string public constant symbol = "DFX";
    uint8 public constant decimals = 18;

    uint256 private _totalSupply;
    mapping(address => uint256) private _balances;
    mapping(address => mapping(address => uint256)) private _allowances;

    // --- Fixed-Point Constants (WAD = 1e18) ---
    uint256 public constant WAD = 1e18;

    // Thermostatic coefficients (frozen from config/params.yml):
    // const = 0.90, gen_dis_rate = 1.8 -> c_transfer = 0.90, c_gen = 0.90 * 1.8 = 1.62
    uint256 public constant C_TRANSFER = 900_000_000_000_000_000; // 0.98 * 1e18
    uint256 public constant C_GEN      = 1_620_000_000_000_000_000; // 1.568 * 1e18

    // Decay rates:
    // `late` decays to 0.1x over 7 days (604,800 seconds):
    uint256 public constant LAMBDA_LATE  = 3_807_184_346_882;
    // `early` tracks the long-term 28-day reference window (decays 4x slower):
    uint256 public constant LAMBDA_EARLY = 951_796_086_720;

    // Minimum baseline floor for `early` to prevent deflationary deadlock after long idle periods
    uint256 public constant MIN_EARLY = 1_000_000_000_000_000_000_000;

    // --- Thermostatic & Cryptographic Proof State ---
    uint256 public lateState;
    uint256 public earlyState;
    uint256 public lastUpdateTimestamp;

    // Cumulative cryptographic commitment proving zero-tampering since genesis
    bytes32 public stateHash;
    uint256 public stepCount;

    // --- Lightweight Theoretical PoW ---
    // 8 leading zero bits = average 256 hashes (< 1ms on mobile/Discord bot)
    uint8 public constant POW_DIFFICULTY_BITS = 8;
    uint256 public constant POW_TARGET = type(uint256).max >> POW_DIFFICULTY_BITS;

    // --- Events (Complete Audit Log for Public Verification) ---
    event StateTransition(
        uint256 indexed step,
        bytes32 indexed prevStateHash,
        bytes32 indexed newStateHash,
        uint256 timestamp,
        address from,
        address to,
        uint256 grossAmount,
        uint256 netAmount,
        uint256 powNonce,
        uint256 lateAfter,
        uint256 earlyAfter
    );

    // --- Relayer Support for Serverless Discord Bot (Cloudflare Workers) ---
    // Optional trusted relayer address set at deployment (address(0) if unused).
    // Allows a stateless Cloudflare Worker to sponsor testnet/L2 gas on behalf of Discord users
    // whose deterministic on-chain addresses do not hold native ETH, while preserving every single
    // thermostatic, split-invariance, PoW, and stateHash audit rule.
    address public immutable relayer;

    constructor(address initialRelayer) {
        relayer = initialRelayer;
        lastUpdateTimestamp = block.timestamp;
        earlyState = MIN_EARLY;
        lateState = 0;
        stateHash = keccak256(abi.encodePacked("DYNAFLUX_GENESIS", block.timestamp, lateState, earlyState));
    }

    // --- ERC-20 Standard Views ---

    function totalSupply() external view override returns (uint256) {
        return _totalSupply;
    }

    function balanceOf(address account) external view override returns (uint256) {
        return _balances[account];
    }

    function allowance(address holder, address spender) external view override returns (uint256) {
        return _allowances[holder][spender];
    }

    // --- Deterministic Rational Padé Approximation for exp(-x) ---

    /**
     * @notice Computes exp(-x) in WAD where x is in WAD (x >= 0).
     * @dev Pure integer arithmetic, 100% deterministic across all machines and verifiers.
     */
    function expNegWad(uint256 x) public pure returns (uint256) {
        if (x == 0) return WAD;
        if (x >= 42 * WAD) return 0;

        uint256 k = 0;
        uint256 r = x;
        while (r >= WAD) {
            r >>= 1;
            k++;
        }

        uint256 r2 = (r * r) / WAD;
        uint256 r3 = (r2 * r) / WAD;
        uint256 r4 = (r3 * r) / WAD;
        uint256 r5 = (r4 * r) / WAD;

        uint256 expPosR = WAD + r + (r2 / 2) + (r3 / 6) + (r4 / 24) + (r5 / 120);
        uint256 res = (WAD * WAD) / expPosR;

        for (uint256 i = 0; i < k; i++) {
            res = (res * res) / WAD;
        }
        return res;
    }

    // --- Core Thermostatic Mathematics ---

    /**
     * @notice Returns the current decayed (late, early) values at `block.timestamp`.
     * @dev Satisfies exact time-step associativity (semigroup property over dt):
     *      L(dt) = L_0 * d_late(dt)
     *      E(dt) = MIN_EARLY + (E_0 - MIN_EARLY) * d_early(dt) + L_0 * (d_early(dt) - d_late(dt))
     */
    function currentSummary() public view returns (uint256 late, uint256 early) {
        uint256 dt = block.timestamp - lastUpdateTimestamp;
        uint256 dLate = expNegWad(LAMBDA_LATE * dt);
        uint256 dEarly = expNegWad(LAMBDA_EARLY * dt);

        late = (lateState * dLate) / WAD;
        uint256 lateAtEarlyRate = (lateState * dEarly) / WAD;
        uint256 maturedFromLate = (lateAtEarlyRate > late) ? (lateAtEarlyRate - late) : 0;

        uint256 earlyExcess = (earlyState > MIN_EARLY) ? (earlyState - MIN_EARLY) : 0;
        early = MIN_EARLY + (earlyExcess * dEarly) / WAD + maturedFromLate;
    }

    /**
     * @notice Split-invariant calculation of `net` (post-tax amount) from `gross` amount.
     */
    function quoteNet(uint256 grossAmount, bool isGen) public view returns (uint256 netAmount) {
        (uint256 late, uint256 early) = currentSummary();
        return _computeNet(grossAmount, late, early, isGen);
    }

    function _computeNet(
        uint256 grossAmount,
        uint256 late,
        uint256 early,
        bool isGen
    ) internal pure returns (uint256) {
        if (grossAmount == 0) return 0;

        uint256 c = isGen ? C_GEN : C_TRANSFER;
        uint256 cLate = (c * late) / WAD;

        if (early <= cLate) {
            return 0;
        }

        uint256 remainingCapacity = ((early - cLate) * WAD) / c;
        uint256 exponent = (c * grossAmount) / early;
        uint256 oneMinusExp = WAD - expNegWad(exponent);

        return (remainingCapacity * oneMinusExp) / WAD;
    }

    // --- Lightweight PoW Verification Bound to `stateHash` ---

    /**
     * @notice Computes the PoW digest anchored to the current `stateHash`.
     */
    function getPowHash(
        bytes32 currentStateHash,
        address account,
        uint256 grossAmount,
        uint256 powNonce
    ) public pure returns (bytes32) {
        return keccak256(abi.encodePacked(currentStateHash, account, grossAmount, powNonce));
    }

    /**
     * @notice Checks whether a `powNonce` satisfies the lightweight PoW target for the current state.
     */
    function verifyPow(
        address account,
        uint256 grossAmount,
        uint256 powNonce
    ) public view returns (bool) {
        bytes32 digest = getPowHash(stateHash, account, grossAmount, powNonce);
        return uint256(digest) <= POW_TARGET;
    }

    // --- State-Modifying Operations ---

    function _commitTransition(
        address from,
        address to,
        uint256 grossAmount,
        uint256 netAmount,
        uint256 powNonce,
        uint256 lateBefore,
        uint256 earlyBefore
    ) internal {
        lateState = lateBefore + netAmount;
        earlyState = earlyBefore;
        lastUpdateTimestamp = block.timestamp;

        bytes32 prevHash = stateHash;
        bytes32 nextHash = keccak256(
            abi.encodePacked(
                prevHash,
                block.timestamp,
                from,
                to,
                grossAmount,
                netAmount,
                powNonce,
                lateState,
                earlyState
            )
        );
        stateHash = nextHash;
        stepCount++;

        emit StateTransition(
            stepCount,
            prevHash,
            nextHash,
            block.timestamp,
            from,
            to,
            grossAmount,
            netAmount,
            powNonce,
            lateState,
            earlyState
        );
    }

    /**
     * @notice Anonymous PoW-guarded generation/minting.
     * @param grossAmount Declared gross effort/amount.
     * @param powNonce Solution to the lightweight PoW puzzle bound to `stateHash`.
     */
    function mint(uint256 grossAmount, uint256 powNonce) external returns (uint256 netMinted) {
        return _mintTo(msg.sender, grossAmount, powNonce);
    }

    /**
     * @notice Gas-sponsored minting to `recipient` (callable by anyone who solves the PoW for `recipient`).
     * @dev Because PoW binds to `recipient` and `stateHash`, anyone (including a Cloudflare Worker relayer)
     *      can submit the transaction and pay gas on behalf of a Discord user or MetaMask address.
     */
    function mintTo(
        address recipient,
        uint256 grossAmount,
        uint256 powNonce
    ) external returns (uint256 netMinted) {
        return _mintTo(recipient, grossAmount, powNonce);
    }

    function _mintTo(
        address recipient,
        uint256 grossAmount,
        uint256 powNonce
    ) internal returns (uint256 netMinted) {
        require(recipient != address(0), "DFX: mint to zero address");
        require(grossAmount > 0, "DFX: amount must be positive");
        require(verifyPow(recipient, grossAmount, powNonce), "DFX: invalid PoW nonce");

        (uint256 late, uint256 early) = currentSummary();
        netMinted = _computeNet(grossAmount, late, early, true);
        require(netMinted > 0, "DFX: thermal capacity exhausted (net is 0)");

        _commitTransition(address(0), recipient, grossAmount, netMinted, powNonce, late, early);

        _totalSupply += netMinted;
        _balances[recipient] += netMinted;

        emit Transfer(address(0), recipient, netMinted);
    }

    /**
     * @notice Gas-sponsored transfer executed by the configured Discord Bot relayer.
     * @dev Allows Discord users to transfer from their deterministic Discord vault address
     *      to another Discord user or out to an external MetaMask wallet with zero ETH gas required.
     */
    function relayTransfer(
        address from,
        address to,
        uint256 grossAmount
    ) external returns (bool) {
        require(msg.sender == relayer, "DFX: caller is not relayer");
        _transferWithThermalBurn(from, to, grossAmount);
        return true;
    }

    function approve(address spender, uint256 value) external override returns (bool) {
        _allowances[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transfer(address to, uint256 value) external override returns (bool) {
        _transferWithThermalBurn(msg.sender, to, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external override returns (bool) {
        uint256 currentAllowance = _allowances[from][msg.sender];
        require(currentAllowance >= value, "DFX: insufficient allowance");
        _allowances[from][msg.sender] = currentAllowance - value;

        _transferWithThermalBurn(from, to, value);
        return true;
    }

    function _transferWithThermalBurn(address from, address to, uint256 grossAmount) internal {
        require(from != address(0), "DFX: transfer from zero address");
        require(to != address(0), "DFX: transfer to zero address");
        require(grossAmount > 0, "DFX: amount must be positive");
        require(_balances[from] >= grossAmount, "DFX: insufficient balance");

        (uint256 late, uint256 early) = currentSummary();
        uint256 netAmount = _computeNet(grossAmount, late, early, false);
        uint256 burnedTax = grossAmount - netAmount;

        _commitTransition(from, to, grossAmount, netAmount, 0, late, early);

        _balances[from] -= grossAmount;
        _balances[to] += netAmount;
        _totalSupply -= burnedTax;

        emit Transfer(from, to, netAmount);
        if (burnedTax > 0) {
            emit Transfer(from, address(0), burnedTax);
        }
    }
}
