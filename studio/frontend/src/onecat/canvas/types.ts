// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
export type Viewport = { x: number; y: number; k: number };
export type Params = {
	width: number;
	height: number;
	num_frames: number;
	seed: number;
	lora_scale: number;
};
export type CanvasNode = {
	id: string;
	kind: "text" | "image" | "video" | "audio" | "generate";
	x: number;
	y: number;
	title: string;
	text: string;
	asset_id: string | null;
	workflow: string;
	service_id: string;
	parameters: Params;
	origin_run: string | null;
};
export type Edge = { id: string; source: string; target: string };
export type Asset = {
	id: string;
	kind: "image" | "video" | "audio";
	name: string;
	mime: string;
	bytes: number;
	url: string;
	thumbnail_url?: string;
	width?: number;
	height?: number;
	duration?: number;
};
export type Project = {
	id: string;
	title: string;
	revision: number;
	nodes: CanvasNode[];
	edges: Edge[];
	viewport: Viewport;
	placed_runs: string[];
	updated_at?: number;
	assets?: Asset[];
};
export type Workflow = {
	id: string;
	name: string;
	name_en: string;
	provider: string;
	partition?: string;
	output: "image" | "video";
	min_images: number;
	max_images: number;
	experimental?: boolean;
};
export type Service = {
	id: string;
	name: string;
	kind: "h3-local" | "h3-api" | "image-api" | "image-local";
  checkpoint?: string;
  output_sizes?: [number, number][];
	model: string;
	partition: "fl2va" | "ref2va";
	base_url: string;
	runtime_id: string;
	gpu_uuids: string[];
	transformer_path: string;
	lora_path: string;
	attention_backend: string;
	image_edit: boolean;
	workflows: string[];
	instance: {
		state: string;
		error?: string;
		operation_error?: string;
		job_id?: string;
        phase?: string; detail?: string; elapsed_s?: number; can_stop?: boolean; cancel_requested?: boolean;
        phase_progress?: { done: number; total: number; percent: number };
	};
	has_key?: boolean;
};
export type Component = {
	id: string;
	name: string;
	role: string;
	partition?: string;
	bytes: number;
	repo_id: string;
	evidence: string;
	installed?: { path: string };
};
export type H3Preset = {
  id: string; name: string; name_en: string; partition: string; turbo: boolean;
  components: string[]; missing_components: string[];
  runtime_available: boolean; hardware_available: boolean;
};
export type Catalog = {
  models?: import("../creative/form").CreativeModel[];
  presets?: H3Preset[];
	workflows: Workflow[];
	components: Component[];
	runtimes: { id: string; name: string; h3_supported: boolean; image_supported?: boolean }[];
};
export type Run = {
  model_id?: string | null;
  timing?: {
    total_seconds: number | null;
    preparation_seconds: number | null;
    generation_seconds: number | null;
    generation_source: "native" | "studio" | null;
  };
  source?: string;
  updated_at?: number; native_updated_at?: number; stage_started_at?: number; detail?: string; connection_lost?: boolean;
  intent?: unknown; reference_assets?: Asset[];
  preparation_progress?: { stage?: string; detail?: string; phase_progress?: { done: number; total: number }; downloaded_bytes?: number; expected_bytes?: number; bytes_per_second?: number };
	id: string;
	project_id: string | null;
	node_id: string;
	service_id: string;
	job_id: string;
	state: string;
	stage: string;
	created_at: number;
	started_at?: number;
	finished_at?: number;
	error?: string;
	assets: Asset[];
	model: string;
	workflow: string;
	prompt: string;
	parameters: Params;
	origin: { x: number; y: number };
	denoise_progress?: { completed: number; total: number };
	cancel_requested?: boolean;
	cancel_note?: string;
	resumable?: boolean;
	telemetry?: {
		source: string;
		power_w?: number;
		average_w?: number;
		memory_mib?: number;
		peak_memory_mib?: number;
		sampled_seconds?: number;
	};
};
export const terminal = (state: string) =>
	["completed", "failed", "cancelled"].includes(state);
export const defaults: Params = {
	width: 1344,
	height: 768,
	num_frames: 107,
	seed: 42,
	lora_scale: 1,
};
export const id = () => {
	const bytes = new Uint8Array(16);
	crypto.getRandomValues(bytes);
	return Array.from(bytes, (v) => v.toString(16).padStart(2, "0")).join("");
};
export const makeNode = (
	kind: CanvasNode["kind"],
	x = 80,
	y = 80,
): CanvasNode => ({
	id: id(),
	kind,
	x,
	y,
	title: "",
	text: "",
	asset_id: null,
	workflow: "h3-t2va",
	service_id: "",
	parameters: { ...defaults },
	origin_run: null,
});
export function wire(project: Project) {
	return {
		title: project.title,
		revision: project.revision,
		nodes: project.nodes,
		edges: project.edges,
		viewport: project.viewport,
		placed_runs: project.placed_runs,
	};
}
export function canConnect(
	nodes: CanvasNode[],
	edges: Edge[],
	source: string,
	target: string,
) {
	const a = nodes.find((n) => n.id === source),
		b = nodes.find((n) => n.id === target);
	if (
		!a ||
		!b ||
		a.kind === "generate" ||
		b.kind !== "generate" ||
		source === target ||
		edges.some((e) => e.source === source && e.target === target)
	)
		return false;
	const visit = (key: string, seen = new Set<string>()): boolean => {
		if (key === source) return true;
		if (seen.has(key)) return false;
		seen.add(key);
		return edges
			.filter((e) => e.source === key)
			.some((e) => visit(e.target, seen));
	};
	return !visit(target);
}
export function runLabel(stage: string, t: (zh: string, en: string) => string) {
	const labels: Record<string, [string, string]> = {
		queued: ["排队中", "Queued"],
        waiting_resources: ["等待资源", "Waiting for resources"],
        downloading: ["下载组件", "Downloading components"],
        checking_service: ["检查服务", "Checking service"],
        packaging: ["封装视频", "Packaging video"],
        waiting_for_engine: ["等待模型操作完成", "Waiting for model operation"],
        waiting_for_requests: ["等待当前请求完成", "Waiting for active requests"],
		submitting: ["提交生成", "Submitting"],
		loading_weights: ["加载权重", "Loading weights"],
		encoding: ["编码素材", "Encoding"],
		denoising: ["去噪中", "Denoising"],
		decoding: ["解码中", "Decoding"],
		generating: ["生成中", "Generating"],
		saving: ["保存结果", "Saving"],
		completed: ["已完成", "Completed"],
		failed: ["未完成", "Failed"],
		cancelled: ["已取消", "Cancelled"],
		ready: ["已就绪", "Ready"],
		loading: ["加载中", "Loading"],
		stopped: ["未加载", "Not loaded"],
		unchecked: ["待连接", "Not connected"],
		unavailable: ["连接中断，正在重连", "Reconnecting"],
		stopping: ["停止中", "Stopping"],
	};
	return labels[stage] ? t(...labels[stage]) : stage;
}

/** Keep new generations and results apart without rearranging the user's board. */
export function freePosition(nodes: CanvasNode[], x: number, y: number) {
	for (let index = 0; index <= 300; index++) {
		const next = {
			x: Math.max(-99000, Math.min(99000, x + Math.floor(index / 6) * 340)),
			y: Math.max(-99000, Math.min(99000, y + (index % 6) * 280)),
		};
		if (
			!nodes.some(
				(n) => Math.abs(n.x - next.x) < 310 && Math.abs(n.y - next.y) < 260,
			)
		)
			return next;
	}
	return { x, y };
}
