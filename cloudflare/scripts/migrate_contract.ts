/**
 * DynaFlux Contract Snapshot & Zero-Tax Migration CLI (For Agents & Developers)
 *
 * Usage:
 *   1. Take a snapshot of the current contract (dry-run / backup):
 *      npm run snapshot
 *
 *   2. Snapshot + Deploy new contract + Migrate all balances & thermostatic state + Update wrangler.toml:
 *      npm run migrate
 *
 *   3. Specify custom old contract or reset thermostatic state to fresh genesis:
 *      npx tsx scripts/migrate_contract.ts --from 0xOldContractAddress --reset-thermostat
 */

import * as fs from "node:fs";
import * as path from "node:path";
// @ts-ignore
import solc from "solc";
import {
  Address,
  Hex,
  createPublicClient,
  createWalletClient,
  formatEther,
  http,
  isAddress,
  parseAbi,
  parseEventLogs,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { sepolia } from "viem/chains";

const ROOT_DIR = path.resolve(__dirname, "../..");
const CONTRACT_FILE = path.join(ROOT_DIR, "contracts/DynaFluxToken.sol");
const WALLET_FILE = path.resolve(__dirname, "../.relayer-wallet.json");
const WRANGLER_FILE = path.resolve(__dirname, "../wrangler.toml");
const SNAPSHOT_DIR = path.resolve(__dirname, "../snapshots");
const RPC_URL = "https://ethereum-sepolia-rpc.publicnode.com";

const READ_ABI = parseAbi([
  "event Transfer(address indexed from, address indexed to, uint256 value)",
  "event StateTransition(uint256 indexed step, bytes32 indexed prevStateHash, bytes32 indexed newStateHash, uint256 timestamp, address from, address to, uint256 grossAmount, uint256 netAmount, uint256 powNonce, uint256 lateAfter, uint256 earlyAfter)",
  "function totalSupply() view returns (uint256)",
  "function balanceOf(address account) view returns (uint256)",
  "function currentSummary() view returns (uint256 late, uint256 early)",
  "function lateState() view returns (uint256)",
  "function earlyState() view returns (uint256)",
  "function lastUpdateTimestamp() view returns (uint256)",
  "function stateHash() view returns (bytes32)",
  "function stepCount() view returns (uint256)",
  "function genesisMigrate(address[] holders, uint256[] amounts, uint256 migratedLate, uint256 migratedEarly)",
]);

export interface HolderRecord {
  address: Address;
  balanceWei: string;
  balanceDfx: string;
}

export interface ContractSnapshot {
  capturedAt: string;
  chainId: number;
  blockNumber: string;
  contractAddress: Address;
  stepCount: string;
  stateHash: Hex;
  totalSupplyWei: string;
  totalSupplyDfx: string;
  thermostat: {
    decayedLateWei: string;
    decayedLateDfx: string;
    decayedEarlyWei: string;
    decayedEarlyDfx: string;
    rawLateStateWei: string;
    rawEarlyStateWei: string;
    lastUpdateTimestamp: string;
  };
  holders: HolderRecord[];
}

function readCurrentContractFromWrangler(): Address {
  const content = fs.readFileSync(WRANGLER_FILE, "utf-8");
  const match = content.match(/CONTRACT_ADDRESS\s*=\s*"(0x[0-9a-fA-F]{40})"/);
  if (!match || !isAddress(match[1])) {
    throw new Error(`Could not parse valid CONTRACT_ADDRESS from ${WRANGLER_FILE}`);
  }
  return match[1] as Address;
}

function compileContract(): { abi: unknown[]; bytecode: Hex } {
  const source = fs.readFileSync(CONTRACT_FILE, "utf-8");
  const input = {
    language: "Solidity",
    sources: {
      "DynaFluxToken.sol": { content: source },
    },
    settings: {
      optimizer: { enabled: true, runs: 200 },
      outputSelection: {
        "*": {
          "*": ["abi", "evm.bytecode.object"],
        },
      },
    },
  };

  const output = JSON.parse(solc.compile(JSON.stringify(input)));
  if (output.errors) {
    const fatal = output.errors.filter((e: { severity: string }) => e.severity === "error");
    if (fatal.length > 0) {
      throw new Error(fatal.map((e: { formattedMessage: string }) => e.formattedMessage).join("\n"));
    }
  }

  const compiled = output.contracts["DynaFluxToken.sol"]["DynaFluxToken"];
  return {
    abi: compiled.abi,
    bytecode: (`0x${compiled.evm.bytecode.object}`) as Hex,
  };
}

async function takeSnapshot(oldContract: Address): Promise<{ snapshot: ContractSnapshot; filePath: string }> {
  const publicClient = createPublicClient({
    chain: sepolia,
    transport: http(RPC_URL),
  });

  const currentBlock = await publicClient.getBlockNumber();
  console.log(`\n[1/4] スナップショット取得中: ${oldContract} (Block: ${currentBlock})`);

  const [totalSupply, summary, lateState, earlyState, lastUpdate, stateHash, stepCount] = await Promise.all([
    publicClient.readContract({ address: oldContract, abi: READ_ABI, functionName: "totalSupply" }),
    publicClient.readContract({ address: oldContract, abi: READ_ABI, functionName: "currentSummary" }),
    publicClient.readContract({ address: oldContract, abi: READ_ABI, functionName: "lateState" }),
    publicClient.readContract({ address: oldContract, abi: READ_ABI, functionName: "earlyState" }),
    publicClient.readContract({ address: oldContract, abi: READ_ABI, functionName: "lastUpdateTimestamp" }),
    publicClient.readContract({ address: oldContract, abi: READ_ABI, functionName: "stateHash" }),
    publicClient.readContract({ address: oldContract, abi: READ_ABI, functionName: "stepCount" }),
  ]);

  // Fetch contract logs in chunks of 45,000 blocks back to discover all addresses
  const CHUNK_SIZE = 45_000n;
  const MAX_LOOKBACK = 200_000n;
  const startBlock = currentBlock > MAX_LOOKBACK ? currentBlock - MAX_LOOKBACK : 0n;
  const candidateAddresses = new Set<Address>();

  for (let to = currentBlock; to > startBlock; to -= CHUNK_SIZE) {
    const from = to > CHUNK_SIZE ? to - CHUNK_SIZE + 1n : 0n;
    const rawLogs = await publicClient.getLogs({
      address: oldContract,
      fromBlock: from,
      toBlock: to,
    });
    const transferLogs = parseEventLogs({ abi: READ_ABI, eventName: "Transfer", logs: rawLogs });
    for (const log of transferLogs) {
      if (log.args.from && log.args.from !== "0x0000000000000000000000000000000000000000") {
        candidateAddresses.add(log.args.from);
      }
      if (log.args.to && log.args.to !== "0x0000000000000000000000000000000000000000") {
        candidateAddresses.add(log.args.to);
      }
    }
    const transitionLogs = parseEventLogs({ abi: READ_ABI, eventName: "StateTransition", logs: rawLogs });
    for (const log of transitionLogs) {
      if (log.args.to && log.args.to !== "0x0000000000000000000000000000000000000000") {
        candidateAddresses.add(log.args.to);
      }
    }
    if (from === 0n) break;
  }

  const addressList = Array.from(candidateAddresses);
  const balances = await Promise.all(
    addressList.map((addr) =>
      publicClient.readContract({
        address: oldContract,
        abi: READ_ABI,
        functionName: "balanceOf",
        args: [addr],
      })
    )
  );

  const holders: HolderRecord[] = [];
  let sumBalances = 0n;
  for (let i = 0; i < addressList.length; i++) {
    const bal = balances[i];
    if (bal > 0n) {
      holders.push({
        address: addressList[i],
        balanceWei: bal.toString(),
        balanceDfx: formatEther(bal),
      });
      sumBalances += bal;
    }
  }

  // Sort descending by balance
  holders.sort((a, b) => (BigInt(b.balanceWei) > BigInt(a.balanceWei) ? 1 : -1));

  if (sumBalances !== totalSupply) {
    console.warn(
      `⚠️ 注意: 検出されたアドレス残高合計 (${formatEther(sumBalances)} DFX) と totalSupply (${formatEther(totalSupply)} DFX) に差異があります。`
    );
  } else {
    console.log(`   ✅ 全保有者の残高整合性チェック完了 (合計: ${formatEther(totalSupply)} DFX, 保有者数: ${holders.length} 名)`);
  }

  const [decayedLate, decayedEarly] = summary;
  const snapshot: ContractSnapshot = {
    capturedAt: new Date().toISOString(),
    chainId: sepolia.id,
    blockNumber: currentBlock.toString(),
    contractAddress: oldContract,
    stepCount: stepCount.toString(),
    stateHash,
    totalSupplyWei: totalSupply.toString(),
    totalSupplyDfx: formatEther(totalSupply),
    thermostat: {
      decayedLateWei: decayedLate.toString(),
      decayedLateDfx: formatEther(decayedLate),
      decayedEarlyWei: decayedEarly.toString(),
      decayedEarlyDfx: formatEther(decayedEarly),
      rawLateStateWei: lateState.toString(),
      rawEarlyStateWei: earlyState.toString(),
      lastUpdateTimestamp: lastUpdate.toString(),
    },
    holders,
  };

  if (!fs.existsSync(SNAPSHOT_DIR)) {
    fs.mkdirSync(SNAPSHOT_DIR, { recursive: true });
  }
  const fileName = `snapshot_${oldContract.slice(0, 10)}_block_${currentBlock}.json`;
  const filePath = path.join(SNAPSHOT_DIR, fileName);
  const latestPath = path.join(SNAPSHOT_DIR, "latest.json");
  fs.writeFileSync(filePath, JSON.stringify(snapshot, null, 2), "utf-8");
  fs.writeFileSync(latestPath, JSON.stringify(snapshot, null, 2), "utf-8");

  console.log(`   📄 スナップショット保存完了: ${filePath}`);
  for (const h of holders) {
    console.log(`      - ${h.address}: ${h.balanceDfx} DFX`);
  }

  return { snapshot, filePath };
}

async function main() {
  const args = process.argv.slice(2);
  const snapshotOnly = args.includes("--snapshot-only");
  const resetThermostat = args.includes("--reset-thermostat");
  const fromIdx = args.indexOf("--from");
  const oldContract =
    fromIdx !== -1 && args[fromIdx + 1]
      ? (args[fromIdx + 1] as Address)
      : readCurrentContractFromWrangler();

  const { snapshot } = await takeSnapshot(oldContract);

  if (snapshotOnly) {
    console.log("\n✅ --snapshot-only モードのため、ここで完了します。");
    return;
  }

  if (!fs.existsSync(WALLET_FILE)) {
    throw new Error(`Relayer wallet file not found at ${WALLET_FILE}. Run deploy_contract.ts first.`);
  }
  const walletData = JSON.parse(fs.readFileSync(WALLET_FILE, "utf-8"));
  const account = privateKeyToAccount(walletData.privateKey as Hex);

  const publicClient = createPublicClient({
    chain: sepolia,
    transport: http(RPC_URL),
  });
  const walletClient = createWalletClient({
    account,
    chain: sepolia,
    transport: http(RPC_URL),
  });

  console.log("\n[2/4] 新しい contracts/DynaFluxToken.sol をコンパイル & デプロイ中...");
  const { abi, bytecode } = compileContract();
  const deployHash = await walletClient.deployContract({
    abi,
    bytecode,
    args: [account.address],
  });
  console.log(`   Deploy Tx: https://sepolia.etherscan.io/tx/${deployHash}`);
  const deployReceipt = await publicClient.waitForTransactionReceipt({ hash: deployHash });
  const newContract = deployReceipt.contractAddress;
  if (!newContract) {
    throw new Error("Deployment failed: no contractAddress in receipt.");
  }
  console.log(`   ✅ 新コントラクトデプロイ完了: ${newContract}`);

  console.log("\n[3/4] ゼロ税率エアドロップ (genesisMigrate) で残高・熱力学状態を引き継ぎ中...");
  const BATCH_SIZE = 150;
  const holders = snapshot.holders;
  const migratedLate = resetThermostat ? 0n : BigInt(snapshot.thermostat.decayedLateWei);
  const migratedEarly = resetThermostat ? 0n : BigInt(snapshot.thermostat.decayedEarlyWei);

  if (holders.length === 0 && migratedEarly === 0n) {
    console.log("   引き継ぐ残高・状態がないため genesisMigrate をスキップします。");
  } else {
    const totalBatches = Math.max(1, Math.ceil(holders.length / BATCH_SIZE));
    for (let b = 0; b < totalBatches; b++) {
      const chunk = holders.slice(b * BATCH_SIZE, (b + 1) * BATCH_SIZE);
      const addrChunk = chunk.map((h) => h.address);
      const amtChunk = chunk.map((h) => BigInt(h.balanceWei));
      const isLastBatch = b === totalBatches - 1;

      const migHash = await walletClient.writeContract({
        address: newContract,
        abi: READ_ABI,
        functionName: "genesisMigrate",
        args: [
          addrChunk,
          amtChunk,
          isLastBatch ? migratedLate : 0n,
          isLastBatch ? migratedEarly : 0n,
        ],
      });
      console.log(`   Migration Batch [${b + 1}/${totalBatches}] Tx: https://sepolia.etherscan.io/tx/${migHash}`);
      const migReceipt = await publicClient.waitForTransactionReceipt({ hash: migHash });
      if (migReceipt.status === "reverted") {
        throw new Error(`genesisMigrate reverted in batch ${b + 1}: ${migHash}`);
      }
    }
    const newSupply = await publicClient.readContract({
      address: newContract,
      abi: READ_ABI,
      functionName: "totalSupply",
    });
    console.log(`   ✅ 引き継ぎ完了！ 新コントラクト totalSupply: ${formatEther(newSupply)} DFX`);
  }

  console.log("\n[4/4] wrangler.toml の CONTRACT_ADDRESS を更新中...");
  const wranglerContent = fs.readFileSync(WRANGLER_FILE, "utf-8");
  const updatedWrangler = wranglerContent.replace(
    /CONTRACT_ADDRESS = "0x[0-9a-fA-F]+"/,
    `CONTRACT_ADDRESS = "${newContract}"`
  );
  fs.writeFileSync(WRANGLER_FILE, updatedWrangler, "utf-8");

  console.log("==================================================================");
  console.log(" マイグレーション完了！");
  console.log(` 旧コントラクト: ${oldContract}`);
  console.log(` 新コントラクト: ${newContract}`);
  console.log(` 引き継ぎ人数  : ${holders.length} 名 (合計 ${snapshot.totalSupplyDfx} DFX)`);
  console.log(" 最後に `npx wrangler deploy` を実行すると Bot が新コントラクトに切り替わります。");
  console.log("==================================================================");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
