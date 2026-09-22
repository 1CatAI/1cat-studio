// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import type { Profile } from "./api";

export function modelPublisher(repo?: string | null) {
  if (!repo?.includes("/")) return "";
  const publisher = repo.split("/")[0];
  return publisher.toLowerCase() === "unsloth" ? "Unsloth" : publisher;
}

export function accelerationLabel(profile?: Pick<Profile, "speculative_config">) {
  const config = profile?.speculative_config;
  if (!config) return "";
  if (config.method === "mtp") return `MTP · ${config.num_speculative_tokens || 4}`;
  if (config.method === "dflash") return "DFlash2";
  return String(config.method || "");
}

/** Display identity stays separate from paths, API aliases, and launch settings. */
export function modelLabel(
  model?: string | Pick<Profile, "name" | "quantization">,
) {
  if (!model) return "—";
  const raw = typeof model === "string" ? model : model.name;
  const displayedQuant = raw.match(
    /·\s*(NVFP4|MXFP4|AWQ|GPTQ|FP8|FP16|BF16|INT8|INT4)(?:\s|$)/i,
  )?.[1];
  const name = raw
    .split("/")
    .at(-1)!
    .split(/\s+·\s+/)[0]
    .replace(
      /[-_](?:e[45]m[23]|unit[-_]kv|fp8[-_]kv|kv[-_]cache|bf16[-_]lmhead|lmhead)(?:[-_].*)?$/i,
      "",
    );
  const match = name.match(
    /(?:[-_])(NVFP4|MXFP4|AWQ|GPTQ|FP8|FP16|BF16|INT8|INT4)(?:[-_].*)?$/i,
  );
  const explicit = typeof model === "string" ? null : model.quantization;
  const quant =
    match?.[1] ||
    displayedQuant ||
    (explicit &&
    /^(NVFP4|MXFP4|AWQ|GPTQ|FP8|FP16|BF16|INT8|INT4)$/i.test(explicit)
      ? explicit
      : null);
  const base = match ? name.slice(0, match.index) : name;
  return quant && quant !== "auto" ? `${base} · ${quant.toUpperCase()}` : base;
}
