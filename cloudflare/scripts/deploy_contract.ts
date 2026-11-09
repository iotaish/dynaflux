/**
 * Zero-MetaMask, Zero-Remix CLI Deployer for DynaFluxToken.sol
 *
 * 1. Automatically generates a testnet wallet (`.relayer-wallet.json`) if not present.
 * 2. Checks Ethereum Sepolia balance.
 * 3. Compiles `contracts/DynaFluxToken.sol` locally using `solc`.
 * 4. Deploys the contract to Sepolia and automatically writes `CONTRACT_ADDRESS` into `wrangler.toml`!
 */

import * as fs from "node:fs";
import * as path from "node:path";
// @ts-ignore
import solc from "solc";
import {
  Hex,
  createPublicClient,
  createWalletClient,
  formatEther,
  http,
} from "viem";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import { sepolia } from "viem/chains";

const ROOT_DIR = path.resolve(__dirname, "../..");
const CONTRACT_FILE = path.join(ROOT_DIR, "contracts/DynaFluxToken.sol");
const WALLET_FILE = path.resolve(__dirname, "../.relayer-wallet.json");
const WRANGLER_FILE = path.resolve(__dirname, "../wrangler.toml");
const RPC_URL = "https://ethereum-sepolia-rpc.publicnode.com";

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

async function main() {
  let privKey: Hex;
  if (fs.existsSync(WALLET_FILE)) {
    const data = JSON.parse(fs.readFileSync(WALLET_FILE, "utf-8"));
    privKey = data.privateKey as Hex;
  } else {
    privKey = generatePrivateKey();
    const acc = privateKeyToAccount(privKey);
    fs.writeFileSync(
      WALLET_FILE,
      JSON.stringify({ address: acc.address, privateKey: privKey }, null, 2),
      { mode: 0o600 }
    );
    console.log("==================================================================");
    console.log(" 新しいテストネット用ウォレットを自動作成しました！(MetaMask不要)");
    console.log("==================================================================");
  }

  const account = privateKeyToAccount(privKey);
  const publicClient = createPublicClient({
    chain: sepolia,
    transport: http(RPC_URL),
  });

  const balance = await publicClient.getBalance({ address: account.address });
  console.log(`\n  [ウォレットアドレス]: ${account.address}`);
  console.log(`  [秘密鍵保存先]      : cloudflare/.relayer-wallet.json`);
  console.log(`  [現在の Sepolia 残高]: ${formatEther(balance)} ETH\n`);

  if (balance === 0n) {
    console.log("------------------------------------------------------------------");
    console.log(" まだガス代 (Sepolia ETH) が 0 です。");
    console.log(" 以下の URL をブラウザで開いて、上の [ウォレットアドレス] を貼り付けるだけで");
    console.log(" 無料の Sepolia ETH が届きます（MetaMaskもログインも一切不要！）:");
    console.log("");
    console.log("   1. https://cloud.google.com/application/web3/faucet/ethereum/sepolia");
    console.log("   2. https://sepolia-faucet.pk910.de/");
    console.log("");
    console.log(" 受け取ったら、もう一度 `npm run chain-deploy` を実行してください！");
    console.log("------------------------------------------------------------------");
    return;
  }

  console.log("1. contracts/DynaFluxToken.sol をローカルでコンパイル中...");
  const { abi, bytecode } = compileContract();

  console.log("2. Ethereum Sepolia ブロックチェーンへデプロイ中...");
  const walletClient = createWalletClient({
    account,
    chain: sepolia,
    transport: http(RPC_URL),
  });

  const txHash = await walletClient.deployContract({
    abi,
    bytecode,
    args: [account.address],
  });
  console.log(`   トランザクション送信完了: https://sepolia.etherscan.io/tx/${txHash}`);
  console.log("   ブロック承認を待機中 (約10〜15秒)...");

  const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });
  const contractAddress = receipt.contractAddress;
  if (!contractAddress) {
    throw new Error("Contract deployment failed (no contractAddress in receipt)");
  }

  // Automatically update wrangler.toml
  const wranglerContent = fs.readFileSync(WRANGLER_FILE, "utf-8");
  const updatedWrangler = wranglerContent.replace(
    /CONTRACT_ADDRESS = "0x[0-9a-fA-F]+"/,
    `CONTRACT_ADDRESS = "${contractAddress}"`
  );
  fs.writeFileSync(WRANGLER_FILE, updatedWrangler, "utf-8");

  console.log("==================================================================");
  console.log(` デプロイ完了！`);
  console.log(` コントラクトアドレス: ${contractAddress}`);
  console.log(` Explorer URL        : https://sepolia.etherscan.io/token/${contractAddress}`);
  console.log(` wrangler.toml の CONTRACT_ADDRESS も自動で書き換えました！`);
  console.log("==================================================================");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
