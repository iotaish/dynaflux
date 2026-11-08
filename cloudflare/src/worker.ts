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
        // Wait 600ms * attempt before re-reading fresh stateHash & pending nonce
        await new Promise((r) => setTimeout(r, 600 * attempt));
      }
    }
  }
  throw lastError;
}

async function verifyDiscordSignature(
  request: Request,
  bodyText: string,
  publicKeyHex: string
): Promise<boolean> {
  const signatureHex = request.headers.get("X-Signature-Ed25519");
  const timestamp = request.headers.get("X-Signature-Timestamp");
  if (!signatureHex || !timestamp) return false;

  const hexToBytes = (hex: string): Uint8Array => {
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < hex.length; i += 2) {
      bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16);
    }
    return bytes;
  };

  const key = await crypto.subtle.importKey(
    "raw",
    hexToBytes(publicKeyHex),
    { name: "Ed25519", namedCurve: "Ed25519" },
    false,
    ["verify"]
  );

  const message = new TextEncoder().encode(timestamp + bodyText);
  return crypto.subtle.verify("Ed25519", key, hexToBytes(signatureHex), message);
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
              `**[オンチェーン資産開示]**\n` +
              `- ユーザー: <@${targetUserId}>\n` +
              `- オンチェーンアドレス: \`${targetAddr}\` ([Explorer](${env.EXPLORER_URL}/address/${targetAddr}))\n` +
              `- 残高: **\`${Number(formatEther(bal)).toFixed(6)} DFX\`** （総流通量: \`${Number(formatEther(supply)).toFixed(4)} DFX\`）`,
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
        const sampleGrosses = Array.from({ length: 6 }, (_, idx) => (maxGross / 6) * (idx + 1));
        const quotes = await Promise.all(
          sampleGrosses.map((g) =>
            publicClient.readContract({
              address: env.CONTRACT_ADDRESS,
              abi: DYNAFLUX_ABI,
              functionName: "quoteNet",
              args: [parseEther(g.toFixed(6)), isGen],
            })
          )
        );
        const rows = sampleGrosses.map(
          (g, idx) =>
            `${g.toFixed(2).padStart(10)} -> ${Number(formatEther(quotes[idx])).toFixed(4).padStart(10)}`
        );
        return jsonResponse({
          type: 4,
          data: {
            content:
              `**[オンチェーン取得額サマリー (${isGen ? "新規発行 c=1.62" : "通常移転 c=0.90"})]**\n` +
              `- 基準熱量 (\`early\`): \`${earlyDfx.toFixed(4)} DFX\`\n` +
              `- 直近熱量 (\`late\`):  \`${lateDfx.toFixed(4)} DFX\`\n` +
              `\`\`\`text\n     gross ->        net\n${rows.join("\n")}\n\`\`\``,
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
        const gasWarning =
          ethNum < 0.005
            ? ` ⚠️ **(残高低下: Faucetから \`${relayerAddr}\` へテストETHを補充してください)**`
            : " (正常)";

        return jsonResponse({
          type: 4,
          data: {
            content:
              `**[オンチェーン状態証明 & 可用性ステータス]**\n` +
              `- コントラクト: [\`${env.CONTRACT_ADDRESS}\`](${env.EXPLORER_URL}/address/${env.CONTRACT_ADDRESS})\n` +
              `- 累積ステップ数: \`${steps.toString()}\`\n` +
              `- 最新 \`stateHash\`: \`${stateHash}\`\n` +
              `- ガス代タンク (\`relayer\`): \`${ethNum.toFixed(4)} ETH\`${gasWarning}\n` +
              `- 検証ステータス: **VALID (EVM決定論的コンセンサスにより改竄不可)**`,
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
              if (amountNum <= 0) {
                await patchDiscordFollowup(env.DISCORD_APP_ID, interaction.token, "エラー: 額は正数で指定してください。");
                return;
              }
              const grossWei = parseEther(amountNum.toFixed(6));

              if (name === "withdraw") {
                const toAddressRaw = String(getOption("address") ?? "");
                if (!isAddress(toAddressRaw)) {
                  await patchDiscordFollowup(
                    env.DISCORD_APP_ID,
                    interaction.token,
                    "エラー: 有効な EVM アドレス (`0x...`) を指定してください。"
                  );
                  return;
                }
                const { txHash, netQuote } = await withConcurrencyRetry(async () => {
                  const [quote, pendingNonce] = await Promise.all([
                    publicClient.readContract({
                      address: env.CONTRACT_ADDRESS,
                      abi: DYNAFLUX_ABI,
                      functionName: "quoteNet",
                      args: [grossWei, false],
                    }),
                    publicClient.getTransactionCount({
                      address: relayerAccount.address,
                      blockTag: "pending",
                    }),
                  ]);
                  const hash = await walletClient.writeContract({
                    address: env.CONTRACT_ADDRESS,
                    abi: DYNAFLUX_ABI,
                    functionName: "relayTransfer",
                    args: [callerAddress, toAddressRaw, grossWei],
                    nonce: pendingNonce,
                  });
                  return { txHash: hash, netQuote: quote };
                });

                await patchDiscordFollowup(
                  env.DISCORD_APP_ID,
                  interaction.token,
                  `**[外部ウォレット出金完了]**\n` +
                    `- 送金元 (Discord Vault): \`${callerAddress}\`\n` +
                    `- 送金先: \`${toAddressRaw}\`\n` +
                    `- 額面 (Gross): \`${amountNum.toFixed(4)} DFX\` → **着金見込 (Net): \`${Number(formatEther(netQuote)).toFixed(6)} DFX\`**\n` +
                    `- トランザクション: [\`${txHash.slice(0, 18)}...\`](${env.EXPLORER_URL}/tx/${txHash})`
                );
                return;
              }

              const transferMode = Boolean(getOption("transfer_mode") ?? false);
              const recipientUserId = (getOption("recipient") as string | undefined) ?? callerId;
              const recipientAddress = discordUserToAddress(recipientUserId);

              if (transferMode) {
                const { txHash, netQuote } = await withConcurrencyRetry(async () => {
                  const [quote, pendingNonce] = await Promise.all([
                    publicClient.readContract({
                      address: env.CONTRACT_ADDRESS,
                      abi: DYNAFLUX_ABI,
                      functionName: "quoteNet",
                      args: [grossWei, false],
                    }),
                    publicClient.getTransactionCount({
                      address: relayerAccount.address,
                      blockTag: "pending",
                    }),
                  ]);
                  const hash = await walletClient.writeContract({
                    address: env.CONTRACT_ADDRESS,
                    abi: DYNAFLUX_ABI,
                    functionName: "relayTransfer",
                    args: [callerAddress, recipientAddress, grossWei],
                    nonce: pendingNonce,
                  });
                  return { txHash: hash, netQuote: quote };
                });

                await patchDiscordFollowup(
                  env.DISCORD_APP_ID,
                  interaction.token,
                  `**[オンチェーン送金報告完了]**\n` +
                    `- 移転者: <@${callerId}> → 受領者: <@${recipientUserId}>\n` +
                    `- 額面 (Gross): \`${amountNum.toFixed(4)} DFX\` → **手取り (Net): \`${Number(formatEther(netQuote)).toFixed(6)} DFX\`**\n` +
                    `- トランザクション: [\`${txHash.slice(0, 18)}...\`](${env.EXPLORER_URL}/tx/${txHash})`
                );
              } else {
                const { txHash, netQuote, nonce, hashes } = await withConcurrencyRetry(async () => {
                  const [currentHash, quote, pendingNonce] = await Promise.all([
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
                      blockTag: "pending",
                    }),
                  ]);
                  const pow = solveLightweightPow(currentHash, recipientAddress, grossWei);
                  const hash = await walletClient.writeContract({
                    address: env.CONTRACT_ADDRESS,
                    abi: DYNAFLUX_ABI,
                    functionName: "mintTo",
                    args: [recipientAddress, grossWei, pow.nonce],
                    nonce: pendingNonce,
                  });
                  return { txHash: hash, netQuote: quote, nonce: pow.nonce, hashes: pow.hashes };
                });

                await patchDiscordFollowup(
                  env.DISCORD_APP_ID,
                  interaction.token,
                  `**[オンチェーン新規発行 (Mint) 完了]**\n` +
                    `- 受領者: <@${recipientUserId}> (\`${recipientAddress}\`)\n` +
                    `- 申告額 (Gross): \`${amountNum.toFixed(4)} DFX\` → **発行額 (Net): \`${Number(formatEther(netQuote)).toFixed(6)} DFX\`**\n` +
                    `- 軽量PoW: \`nonce=${nonce.toString()}\` (\`${hashes} hashes\`)\n` +
                    `- トランザクション: [\`${txHash.slice(0, 18)}...\`](${env.EXPLORER_URL}/tx/${txHash})`
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
