/**
 * DynaFlux (DFX) — High-Availability Serverless Cloudflare Worker for Discord Interactions
 * Connects Discord slash commands directly to `DynaFluxToken.sol` on Ethereum Sepolia (or Base Sepolia).
 *
 * High-Availability (HA) Features:
 * 1. Multi-RPC Automatic Failover (`fallback` transport across independent public endpoints).
 * 2. Optimistic Concurrency Retry with exponential backoff (handles simultaneous `/report` calls
 *    where `stateHash` or relayer account `nonce` advances mid-flight).
 * 3. Live Relayer Gas Tank Monitoring in `/verify` (warns before Sepolia ETH runs low).
 */

import {
  Address,
  Hex,
  createPublicClient,
  createWalletClient,
  encodePacked,
  fallback,
  formatEther,
  http,
  isAddress,
  keccak256,
  parseAbi,
  parseEther,
  parseEventLogs,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { baseSepolia, sepolia } from "viem/chains";

export interface Env {
  DISCORD_PUBLIC_KEY: string;
  DISCORD_APP_ID: string;
  RELAYER_PRIVATE_KEY: Hex;
  CHAIN_ID?: string;
  RPC_URL: string;
  EXPLORER_URL: string;
  CONTRACT_ADDRESS: Address;
}

const SEPOLIA_FALLBACK_RPCS = [
  "https://ethereum-sepolia-rpc.publicnode.com",
  "https://1rpc.io/sepolia",
  "https://rpc.sepolia.org",
];

const BASE_SEPOLIA_FALLBACK_RPCS = [
  "https://sepolia.base.org",
  "https://base-sepolia-rpc.publicnode.com",
];

const DYNAFLUX_ABI = parseAbi([
  "function totalSupply() view returns (uint256)",
  "function balanceOf(address account) view returns (uint256)",
  "function currentSummary() view returns (uint256 late, uint256 early)",
  "function quoteNet(uint256 grossAmount, bool isGen) view returns (uint256 netAmount)",
  "function stateHash() view returns (bytes32)",
  "function stepCount() view returns (uint256)",
  "function relayer() view returns (address)",
  "function mintTo(address recipient, uint256 grossAmount, uint256 powNonce) returns (uint256 netMinted)",
  "function relayTransfer(address from, address to, uint256 grossAmount) returns (bool)",
  "event StateTransition(uint256 indexed step, bytes32 indexed prevStateHash, bytes32 indexed newStateHash, uint256 timestamp, address from, address to, uint256 grossAmount, uint256 netAmount, uint256 powNonce, uint256 lateAfter, uint256 earlyAfter)",
]);

const POW_TARGET = (1n << 256n) - 1n >> 8n;

export function discordUserToAddress(discordUserId: string): Address {
  const hash = keccak256(encodePacked(["string", "string"], ["DYNAFLUX_DISCORD_USER:", discordUserId]));
  return (`0x${hash.slice(26)}`) as Address;
}

export function solveLightweightPow(
  stateHash: Hex,
  recipient: Address,
  grossWei: bigint
): { nonce: bigint; hashes: number } {
  let nonce = 0n;
  while (true) {
    const digest = keccak256(
      encodePacked(
        ["bytes32", "address", "uint256", "uint256"],
        [stateHash, recipient, grossWei, nonce]
      )
    );
    if (BigInt(digest) <= POW_TARGET) {
      return { nonce, hashes: Number(nonce) + 1 };
    }
    nonce++;
  }
}

function buildHaTransport(env: Env) {
  const urls =
    env.CHAIN_ID === "84532"
      ? [env.RPC_URL, ...BASE_SEPOLIA_FALLBACK_RPCS]
      : [env.RPC_URL, ...SEPOLIA_FALLBACK_RPCS];
  const uniqueUrls = Array.from(new Set(urls.filter(Boolean)));
  return fallback(
    uniqueUrls.map((u) => http(u, { timeout: 8_000, retryCount: 1 })),
    { rank: false }
  );
}

async function withConcurrencyRetry<T>(
  fn: (attempt: number) => Promise<T>,
  maxAttempts = 3
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn(attempt);
    } catch (err) {
      lastError = err;
      if (attempt < maxAttempts) {
        // Wait before re-reading fresh stateHash & pending nonce
        await new Promise((r) => setTimeout(r, 800 * attempt));
      }
    }
  }
  throw lastError;
}

async function waitUntilRelayerIdle(
  client: { getTransactionCount: (args: { address: Address; blockTag: "latest" | "pending" }) => Promise<number> },
  relayerAddress: Address,
  onWait?: () => Promise<void>,
  maxWaitMs = 45_000
): Promise<number> {
  const start = Date.now();
  let informed = false;
  while (Date.now() - start < maxWaitMs) {
    const [latestNonce, pendingNonce] = await Promise.all([
      client.getTransactionCount({ address: relayerAddress, blockTag: "latest" }),
      client.getTransactionCount({ address: relayerAddress, blockTag: "pending" }),
    ]);
    if (latestNonce >= pendingNonce) {
      return latestNonce;
    }
    if (!informed && onWait) {
      informed = true;
      await onWait();
    }
    await new Promise((r) => setTimeout(r, 2_000));
  }
  return client.getTransactionCount({ address: relayerAddress, blockTag: "pending" });
}

async function verifyDiscordSignature(
  request: Request,
  bodyText: string,
  publicKeyHex: string
): Promise<boolean> {
  const signatureHex = request.headers.get("X-Signature-Ed25519");
  const timestamp = request.headers.get("X-Signature-Timestamp");
  if (!signatureHex || !timestamp) return false;

  const hexToBytes = (hex: string): Uint8Array<ArrayBuffer> => {
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < hex.length; i += 2) {
      bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16);
    }
    return bytes as Uint8Array<ArrayBuffer>;
  };

  const key = await crypto.subtle.importKey(
    "raw",
    hexToBytes(publicKeyHex),
    { name: "Ed25519", namedCurve: "Ed25519" },
    false,
    ["verify"]
  );

  const message = new TextEncoder().encode(timestamp + bodyText);
  return crypto.subtle.verify("Ed25519", key, hexToBytes(signatureHex), message as Uint8Array<ArrayBuffer>);
}

/**
 * Formats a WAD/wei bigint to 6 significant digits:
 * - Exact numbers with <= 6 significant digits are shown as-is (e.g. `1000`, `0.001`).
 * - Rounded numbers keep all 6 significant digits (including trailing zeros like `≈ 287.950`)
 *   so table columns align cleanly.
 * - Large numbers avoid scientific notation (e.g. `≈ 1,111,111` instead of `≈ 1.11111e+6`).
 */
function fmt6g(wei: bigint): string {
  const exactStr = formatEther(wei);
  const num = Number(exactStr);
  if (num === 0) return "0";
  const prec6 = num.toPrecision(6);
  const cleanExact = Number(prec6).toString();
  if (Number(prec6) === num && exactStr.length <= cleanExact.length) {
    return exactStr;
  }
  if (prec6.includes("e") || prec6.includes("E")) {
    const fixed = num.toLocaleString("en-US", { maximumFractionDigits: 4 });
    return `≈ ${fixed}`;
  }
  return `≈ ${prec6}`;
}

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
  });
}

async function patchDiscordFollowup(appId: string, token: string, content: string): Promise<void> {
  const url = `https://discord.com/api/v10/webhooks/${appId}/${token}/messages/@original`;
  await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    if (request.method !== "POST") {
      return new Response("DynaFlux Discord On-Chain Worker Active (HA Mode)", { status: 200 });
    }

    const bodyText = await request.text();
    const isValid = await verifyDiscordSignature(request, bodyText, env.DISCORD_PUBLIC_KEY);
    if (!isValid) {
      return new Response("Invalid Discord signature", { status: 401 });
    }

    const interaction = JSON.parse(bodyText);

    // 1. Discord PING (Type 1) -> PONG
    if (interaction.type === 1) {
      return jsonResponse({ type: 1 });
    }

    // 2. Slash Commands (Type 2)
    if (interaction.type === 2) {
      const { name, options = [] } = interaction.data;
      const callerId: string = interaction.member?.user?.id ?? interaction.user?.id;
      const callerAddress = discordUserToAddress(callerId);

      const getOption = (optName: string) =>
        options.find((o: { name: string; value: unknown }) => o.name === optName)?.value;

      const activeChain = env.CHAIN_ID === "84532" ? baseSepolia : sepolia;
      const transport = buildHaTransport(env);
      const publicClient = createPublicClient({
        chain: activeChain,
        transport,
      });

      if (name === "balance") {
        const targetUserId = (getOption("user") as string | undefined) ?? callerId;
        const targetAddr = discordUserToAddress(targetUserId);
        const [bal, supply] = await Promise.all([
          publicClient.readContract({
            address: env.CONTRACT_ADDRESS,
            abi: DYNAFLUX_ABI,
            functionName: "balanceOf",
            args: [targetAddr],
          }),
          publicClient.readContract({
            address: env.CONTRACT_ADDRESS,
            abi: DYNAFLUX_ABI,
            functionName: "totalSupply",
          }),
        ]);
        return jsonResponse({
          type: 4,
          data: {
            content:
              `**[資産開示]** <@${targetUserId}>\n` +
              `- 残高: **\`${fmt6g(bal)} DFX\`** （流通量: \`${fmt6g(supply)} DFX\`）\n` +
              `- Vault: [\`${targetAddr}\`](${env.EXPLORER_URL}/address/${targetAddr})`,
          },
        });
      }

      if (name === "summary") {
        const isGen = Boolean(getOption("is_gen") ?? false);
        const [late, early] = await publicClient.readContract({
          address: env.CONTRACT_ADDRESS,
          abi: DYNAFLUX_ABI,
          functionName: "currentSummary",
        });
        const lateDfx = Number(formatEther(late));
        const earlyDfx = Number(formatEther(early));
        const maxGross = Math.max(lateDfx, earlyDfx) * 2;
        const sampleGrosses = Array.from({ length: 6 }, (_, idx) =>
          parseEther(((maxGross / 6) * (idx + 1)).toFixed(6))
        );
        const quotes = await Promise.all(
          sampleGrosses.map((gWei) =>
            publicClient.readContract({
              address: env.CONTRACT_ADDRESS,
              abi: DYNAFLUX_ABI,
              functionName: "quoteNet",
              args: [gWei, isGen],
            })
          )
        );
        const rows = sampleGrosses.map(
          (gWei, idx) => `${fmt6g(gWei).padStart(12)} -> ${fmt6g(quotes[idx]).padStart(12)}`
        );
        return jsonResponse({
          type: 4,
          data: {
            content:
              `**[取得額サマリー (${isGen ? "発行 c=1.62" : "移転 c=0.90"})]**\n` +
              `- \`early\`: \`${fmt6g(early)} DFX\` | \`late\`: \`${fmt6g(late)} DFX\`\n` +
              `\`\`\`text\n       gross ->          net\n${rows.join("\n")}\n\`\`\``,
          },
        });
      }

      if (name === "verify") {
        const [stateHash, steps, relayerAddr] = await Promise.all([
          publicClient.readContract({
            address: env.CONTRACT_ADDRESS,
            abi: DYNAFLUX_ABI,
            functionName: "stateHash",
          }),
          publicClient.readContract({
            address: env.CONTRACT_ADDRESS,
            abi: DYNAFLUX_ABI,
            functionName: "stepCount",
          }),
          publicClient.readContract({
            address: env.CONTRACT_ADDRESS,
            abi: DYNAFLUX_ABI,
            functionName: "relayer",
          }),
        ]);
        const relayerEth = await publicClient.getBalance({ address: relayerAddr });
        const ethNum = Number(formatEther(relayerEth));
        const isLowGas = ethNum < 0.005;
        const gasStatus = isLowGas ? "⚠️ 要補充" : "OK";
        const topUpHint = isLowGas ? `\n👉 ガス代補充先 (Sepolia ETH):\n\`${relayerAddr}\`` : "";

        return jsonResponse({
          type: 4,
          data: {
            content:
              `**[状態証明]** **VALID** (Steps: \`${steps.toString()}\`)\n` +
              `- \`stateHash\`: \`${stateHash}\`\n` +
              `- Contract: [\`${env.CONTRACT_ADDRESS}\`](${env.EXPLORER_URL}/address/${env.CONTRACT_ADDRESS})\n` +
              `- Relayer: [\`${relayerAddr}\`](${env.EXPLORER_URL}/address/${relayerAddr}) | Gas: \`${fmt6g(relayerEth)} ETH\` (${gasStatus})` +
              topUpHint,
          },
        });
      }

      if (name === "report" || name === "withdraw") {
        ctx.waitUntil(
          (async () => {
            try {
              const relayerAccount = privateKeyToAccount(env.RELAYER_PRIVATE_KEY);
              const walletClient = createWalletClient({
                account: relayerAccount,
                chain: activeChain,
                transport,
              });

              const amountNum = Number(getOption("amount") ?? 0);
              if (!Number.isFinite(amountNum) || amountNum <= 0 || amountNum > 1_000_000_000) {
                await patchDiscordFollowup(
                  env.DISCORD_APP_ID,
                  interaction.token,
                  "エラー: 額は `0 < amount <= 1,000,000,000` の有効な数値で指定してください。"
                );
                return;
              }
              const grossWei = parseEther(amountNum.toFixed(6));

              if (name === "withdraw") {
                const toAddressRaw = String(getOption("address") ?? "").trim();
                if (!isAddress(toAddressRaw)) {
                  await patchDiscordFollowup(
                    env.DISCORD_APP_ID,
                    interaction.token,
                    "エラー: 有効な EVM アドレス (`0x...`) を指定してください。"
                  );
                  return;
                }
                const { txHash, actualNetWei } = await withConcurrencyRetry(async (attempt) => {
                  if (attempt > 1) {
                    await new Promise((r) => setTimeout(r, 1000 + Math.random() * 1500));
                  }
                  await waitUntilRelayerIdle(publicClient, relayerAccount.address, async () => {
                    await patchDiscordFollowup(
                      env.DISCORD_APP_ID,
                      interaction.token,
                      "⏳ **先行トランザクションの承認待ちです（最大12秒）...**"
                    );
                  });
                  const [quote, currentNonce] = await Promise.all([
                    publicClient.readContract({
                      address: env.CONTRACT_ADDRESS,
                      abi: DYNAFLUX_ABI,
                      functionName: "quoteNet",
                      args: [grossWei, false],
                    }),
                    publicClient.getTransactionCount({
                      address: relayerAccount.address,
                      blockTag: "latest",
                    }),
                  ]);
                  const hash = await walletClient.writeContract({
                    address: env.CONTRACT_ADDRESS,
                    abi: DYNAFLUX_ABI,
                    functionName: "relayTransfer",
                    args: [callerAddress, toAddressRaw, grossWei],
                    nonce: currentNonce,
                  });
                  await patchDiscordFollowup(
                    env.DISCORD_APP_ID,
                    interaction.token,
                    `⏳ **出金承認中...** [\`${hash.slice(0, 14)}...\`](${env.EXPLORER_URL}/tx/${hash})`
                  );
                  const receipt = await publicClient.waitForTransactionReceipt({ hash, timeout: 60_000 });
                  if (receipt.status === "reverted") {
                    throw new Error(`オンチェーン実行がリバート（失敗）しました (Tx: ${hash})`);
                  }
                  const logs = parseEventLogs({ abi: DYNAFLUX_ABI, eventName: "StateTransition", logs: receipt.logs });
                  const net = logs[0]?.args.netAmount ?? quote;
                  return { txHash: hash, actualNetWei: net };
                }, 2);

                await patchDiscordFollowup(
                  env.DISCORD_APP_ID,
                  interaction.token,
                  `**[出金完了]** <@${callerId}> → \`${toAddressRaw}\`\n` +
                    `- Gross: \`${fmt6g(grossWei)} DFX\` → **Net: \`${fmt6g(actualNetWei)} DFX\`**\n` +
                    `- Tx: [\`${txHash.slice(0, 14)}...\`](${env.EXPLORER_URL}/tx/${txHash})`
                );
                return;
              }

              const transferMode = Boolean(getOption("transfer_mode") ?? false);
              const recipientUserId = (getOption("recipient") as string | undefined) ?? callerId;
              const recipientAddress = discordUserToAddress(recipientUserId);

              if (transferMode) {
                const { txHash, actualNetWei } = await withConcurrencyRetry(async (attempt) => {
                  if (attempt > 1) {
                    await new Promise((r) => setTimeout(r, 1000 + Math.random() * 1500));
                  }
                  await waitUntilRelayerIdle(publicClient, relayerAccount.address, async () => {
                    await patchDiscordFollowup(
                      env.DISCORD_APP_ID,
                      interaction.token,
                      "⏳ **先行トランザクションの承認待ちです（最大12秒）...**"
                    );
                  });
                  const [quote, currentNonce] = await Promise.all([
                    publicClient.readContract({
                      address: env.CONTRACT_ADDRESS,
                      abi: DYNAFLUX_ABI,
                      functionName: "quoteNet",
                      args: [grossWei, false],
                    }),
                    publicClient.getTransactionCount({
                      address: relayerAccount.address,
                      blockTag: "latest",
                    }),
                  ]);
                  const hash = await walletClient.writeContract({
                    address: env.CONTRACT_ADDRESS,
                    abi: DYNAFLUX_ABI,
                    functionName: "relayTransfer",
                    args: [callerAddress, recipientAddress, grossWei],
                    nonce: currentNonce,
                  });
                  await patchDiscordFollowup(
                    env.DISCORD_APP_ID,
                    interaction.token,
                    `⏳ **送金承認中...** [\`${hash.slice(0, 14)}...\`](${env.EXPLORER_URL}/tx/${hash})`
                  );
                  const receipt = await publicClient.waitForTransactionReceipt({ hash, timeout: 60_000 });
                  if (receipt.status === "reverted") {
                    throw new Error(`オンチェーン実行がリバート（失敗）しました (Tx: ${hash})`);
                  }
                  const logs = parseEventLogs({ abi: DYNAFLUX_ABI, eventName: "StateTransition", logs: receipt.logs });
                  const net = logs[0]?.args.netAmount ?? quote;
                  return { txHash: hash, actualNetWei: net };
                }, 2);

                await patchDiscordFollowup(
                  env.DISCORD_APP_ID,
                  interaction.token,
                  `**[送金完了]** <@${callerId}> → <@${recipientUserId}>\n` +
                    `- Gross: \`${fmt6g(grossWei)} DFX\` → **Net: \`${fmt6g(actualNetWei)} DFX\`**\n` +
                    `- Tx: [\`${txHash.slice(0, 14)}...\`](${env.EXPLORER_URL}/tx/${txHash})`
                );
              } else {
                const { txHash, actualNetWei, hashes } = await withConcurrencyRetry(async (attempt) => {
                  if (attempt > 1) {
                    await new Promise((r) => setTimeout(r, 1000 + Math.random() * 1500));
                  }
                  await waitUntilRelayerIdle(publicClient, relayerAccount.address, async () => {
                    await patchDiscordFollowup(
                      env.DISCORD_APP_ID,
                      interaction.token,
                      "⏳ **先行トランザクションの承認待ちです（最大12秒）...**"
                    );
                  });
                  const [currentHash, quote, currentNonce] = await Promise.all([
                    publicClient.readContract({
                      address: env.CONTRACT_ADDRESS,
                      abi: DYNAFLUX_ABI,
                      functionName: "stateHash",
                    }),
                    publicClient.readContract({
                      address: env.CONTRACT_ADDRESS,
                      abi: DYNAFLUX_ABI,
                      functionName: "quoteNet",
                      args: [grossWei, true],
                    }),
                    publicClient.getTransactionCount({
                      address: relayerAccount.address,
                      blockTag: "latest",
                    }),
                  ]);
                  const pow = solveLightweightPow(currentHash, recipientAddress, grossWei);
                  const hash = await walletClient.writeContract({
                    address: env.CONTRACT_ADDRESS,
                    abi: DYNAFLUX_ABI,
                    functionName: "mintTo",
                    args: [recipientAddress, grossWei, pow.nonce],
                    nonce: currentNonce,
                  });
                  await patchDiscordFollowup(
                    env.DISCORD_APP_ID,
                    interaction.token,
                    `⏳ **発行承認中...** [\`${hash.slice(0, 14)}...\`](${env.EXPLORER_URL}/tx/${hash})`
                  );
                  const receipt = await publicClient.waitForTransactionReceipt({ hash, timeout: 60_000 });
                  if (receipt.status === "reverted") {
                    throw new Error(`オンチェーン実行がリバート（失敗）しました (Tx: ${hash})`);
                  }
                  const logs = parseEventLogs({ abi: DYNAFLUX_ABI, eventName: "StateTransition", logs: receipt.logs });
                  const net = logs[0]?.args.netAmount ?? quote;
                  return { txHash: hash, actualNetWei: net, hashes: pow.hashes };
                }, 2);

                await patchDiscordFollowup(
                  env.DISCORD_APP_ID,
                  interaction.token,
                  `**[発行完了]** → <@${recipientUserId}>\n` +
                    `- Gross: \`${fmt6g(grossWei)} DFX\` → **Net: \`${fmt6g(actualNetWei)} DFX\`** (PoW: \`${hashes}\`)\n` +
                    `- Tx: [\`${txHash.slice(0, 14)}...\`](${env.EXPLORER_URL}/tx/${txHash})`
                );
              }
            } catch (err) {
              const msg = err instanceof Error ? err.message : String(err);
              await patchDiscordFollowup(env.DISCORD_APP_ID, interaction.token, `オンチェーン実行エラー: \`${msg}\``);
            }
          })()
        );

        return jsonResponse({ type: 5 });
      }
    }

    return new Response("Unhandled interaction", { status: 400 });
  },
};
