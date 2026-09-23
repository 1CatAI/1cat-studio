// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { readFastApiError } from "@/onecat/http-error";

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/api/auth/"))
      window.dispatchEvent(new Event("onecat:auth-expired"));
    throw new ApiError(await readFastApiError(response), response.status);
  }
  return response.json() as Promise<T>;
}
export function mutation<T>(path: string, body?: unknown, method = "POST") {
  return api<T>(path, {
    method,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
}
export function refreshData() {
  window.dispatchEvent(new Event("onecat:refresh"));
}
let engineSnapshot: { data?: Engine; error: string } = { error: "" };
const engineListeners = new Set<() => void>();
let engineTimer: ReturnType<typeof setTimeout> | undefined;
let engineRequest: AbortController | undefined;
function readEngine() {
  clearTimeout(engineTimer);
  engineRequest?.abort();
  const controller = new AbortController();
  engineRequest = controller;
  void api<Engine>("/api/inference/status", { signal: controller.signal })
    .then((data) => {
      if (!controller.signal.aborted) engineSnapshot = { data, error: "" };
    })
    .catch((error) => {
      if (!controller.signal.aborted)
        engineSnapshot = { ...engineSnapshot, error: error.message };
    })
    .finally(() => {
      if (controller.signal.aborted) return;
      engineListeners.forEach((listener) => listener());
      if (engineListeners.size)
        engineTimer = setTimeout(
          readEngine,
          engineSnapshot.data?.state === "loading" ? 1000 : 2000,
        );
    });
}
function subscribeEngine(listener: () => void) {
  engineListeners.add(listener);
  if (engineListeners.size === 1) {
    for (const name of ["focus", "online", "onecat:refresh"])
      window.addEventListener(name, readEngine);
    document.addEventListener("visibilitychange", readEngine);
    readEngine();
  }
  return () => {
    engineListeners.delete(listener);
    if (!engineListeners.size) {
      clearTimeout(engineTimer);
      engineRequest?.abort();
      for (const name of ["focus", "online", "onecat:refresh"])
        window.removeEventListener(name, readEngine);
      document.removeEventListener("visibilitychange", readEngine);
    }
  };
}
export function useQuery<T>(path: string | null, interval = 0) {
  const shared = useSyncExternalStore(
    useCallback(
      (listener) =>
        path === "/api/inference/status" ? subscribeEngine(listener) : () => {},
      [path],
    ),
    () => engineSnapshot,
  );
  const [result, setResult] = useState<{ path: string; data?: T; error: string }>();
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((v) => v + 1), []);
  useEffect(() => {
    window.addEventListener("onecat:refresh", refresh);
    return () => window.removeEventListener("onecat:refresh", refresh);
  }, [refresh]);
  useEffect(() => {
    if (!path || path === "/api/inference/status") return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function read() {
      try {
        const value = await api<T>(path!, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setResult({ path: path!, data: value, error: "" });
        }
      } catch (e) {
        if (!controller.signal.aborted) setResult(current => ({ path: path!,
          data: current?.path === path ? current.data : undefined, error: String((e as Error).message) }));
      }
      if (interval && !controller.signal.aborted)
        timer = setTimeout(read, interval);
    }
    void read();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [path, interval, revision]);
  return path === "/api/inference/status"
    ? {
        data: shared.data as T | undefined,
        error: shared.error,
        refresh: readEngine,
      }
    : { data: result?.path === path ? result?.data : undefined,
        error: result?.path === path ? result?.error || "" : "", refresh };
}

export type Job = {
  id: string;
  kind: string;
  state: string;
  stage: string;
  progress: number;
  cancel_requested?: boolean;
  error?: string;
  runtime_release_id?: string;
  runtime_version?: string;
  asset_name?: string;
  asset_index?: number;
  asset_count?: number;
  expected_bytes?: number;
  downloaded_bytes?: number;
  bytes_per_second?: number;
  created_at?: number;
  updated_at?: number;
  result?: { runtime_id?: string; download_path?: string; run_id?: string };
};
export type GPU = {
  index: number;
  uuid: string;
  name: string;
  memory_total_mib: number | null;
  memory_used_mib: number | null;
  power_w: number | null;
  power_limit_w: number | null;
  default_power_limit_w: number | null;
  power_min_w: number | null;
  power_max_w: number | null;
  graphics_clock_mhz: number | null;
  memory_clock_mhz: number | null;
  temperature_c: number | null;
  utilization: number | null;
  compute_capability: number[] | null;
  supported_graphics_clocks_mhz: number[];
  processes: { pid: number; name: string }[];
};

export type RequestMetrics = {
  model_name?: string;
  pending?: boolean;
  decode_reason?: string | null;
  prefill_reason?: string | null;
  ttft_s?: number | null;
  elapsed_s?: number | null;
  prefill_tokens_s?: number | null;
  decode_tokens_s?: number | null;
  decode_source?: string;
  prefill_source?: string;
  cached_tokens?: number;
  average_gpu_w?: number | null;
  energy_wh?: number | null;
  concurrent?: boolean;
};
export type RequestRecord = {
  id: string;
  started: number;
  model: string;
  source: string;
  status: number | null;
  elapsed: number | null;
  ttft: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  error: string | null;
  metrics: RequestMetrics;
};
export type RequestHistory = { active: number; items: RequestRecord[]; scope?: { days: number | null; model: string; source: string } };
export type Runtime = {
  id: string;
  name: string;
  python_path: string;
  working_directory?: string;
  validated: boolean;
  managed?: boolean;
  capabilities?: {
    python_version: string;
    vllm_version: string;
    torch_version: string;
    cuda_version: string;
    source_snapshot?: string;
  };
};
export type Model = {
  id: string;
  name: string;
  path: string;
  source: string;
  repo_id?: string;
  bytes: number;
  quantization: string;
  max_context?: number;
  verified?: boolean;
  local_verified?: boolean;
  role?: "target" | "draft";
  catalog_id?: string;
  model_type?: string;
};
export type Hardware = {
  power_limit_w?: number | null;
  graphics_clock_mhz?: number | null;
  reset_clocks?: boolean;
};
export type Profile = {
  id?: string;
  name: string;
  model_path: string;
  runtime_id: string;
  served_model_name: string;
  gpu_uuids: string[];
  tensor_parallel_size: number;
  dtype: string;
  quantization: string | null;
  kv_cache_dtype: string;
  max_model_len: number;
  max_num_batched_tokens: number;
  max_num_seqs: number;
  gpu_memory_utilization: number;
  attention_backend: string | null;
  enforce_eager: boolean;
  enable_prefix_caching: boolean;
  speculative_config: Record<string, unknown> | null;
  extra_args: string[];
  default_sampling: Record<string, unknown>;
  hardware_profile: Hardware | null;
  source: string;
  catalog_id?: string | null;
  tool_calling?: boolean;
  tool_parser?: string | null;
  vision_enabled?: boolean;
  max_images?: number;
  vision_processor_kwargs?: Record<string, unknown>;
};
export type Engine = {
  state: string;
  lan_api_urls?: string[];
  profile_id?: string;
  profile?: Profile;
  port?: number;
  error?: string;
  supervisor?: string;
  systemd_unit?: string;
  hardware_recovery?: { error: string; run_id: string } | null;
  maintenance?: { job_id: string; kind: string } | null;
  phase?: string;
  detail?: string;
  phase_progress?: { done: number; total: number; percent: number } | null;
  started_at?: number;
  elapsed_s?: number;
  progress_updated_at?: number;
  job_id?: string;
  startup_job?: {
    id: string;
    state: string;
    stage?: string;
    cancel_requested?: boolean;
  } | null;
  actions?: { cancel: boolean; stop: boolean; retry: boolean };
};
export type Thread = {
  id: string;
  title: string;
  modelId?: string;
  createdAt: number;
  updatedAt?: number;
  settings?: Record<string, unknown>;
  pinned?: boolean;
  generation?: { state: string } | null;
};
export type Message = {
  id: string;
  threadId: string;
  parentId: string | null;
  role: "user" | "assistant" | "system";
  content: {
    type: string;
    text?: string;
    attachment_id?: string;
    name?: string;
  }[];
  metadata?: Record<string, unknown>;
  createdAt: number;
};
export type Settings = {
  model_directory: string;
  modelscope_endpoint: string;
  idle_unload_minutes: number;
  autostart_profile: string | null;
  host: string;
  port: number;
  locale: "zh-CN" | "en";
  theme: "light" | "dark" | "system";
  modelscope_token_set?: boolean;
};

export function newMessageId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  return [...bytes]
    .map(
      (value, index) =>
        ([4, 6, 8, 10].includes(index) ? "-" : "") +
        value.toString(16).padStart(2, "0"),
    )
    .join("");
}
function legacyCopy(text: string) {
  const focused = document.activeElement;
  const node = document.createElement("textarea");
  node.value = text;
  node.style.cssText = "position:fixed;opacity:0;pointer-events:none";
  document.body.appendChild(node);
  node.select();
  const copied = document.execCommand("copy");
  node.remove();
  if (focused instanceof HTMLElement) focused.focus({ preventScroll: true });
  if (!copied) throw new Error("The browser could not copy this text");
}
export function installClipboardFallback() {
  if (!navigator.clipboard)
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async (text: string) => legacyCopy(text) },
    });
}
export async function copyText(text: string) {
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    /* Retry using the browser's plain-HTTP copy path. */
  }
  legacyCopy(text);
}
