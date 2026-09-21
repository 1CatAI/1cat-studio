import { CreativePrompt, OutputFields } from "../creative/form";
// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
	Sparkles,
	Plus,
	Upload,
	FileText,
	MousePointer2,
	Hand,
	Minus,
	Maximize,
	Undo2,
	Redo2,
	Settings2,
	X,
	Link2,
	Trash2,
	Copy,
	Play,
	Check,
	LoaderCircle,
	Film,
	Image as ImageIcon,
	FolderOpen,
	Download,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { toast } from "sonner";
import { api, mutation, refreshData, useQuery } from "../api";
import { Action, ErrorNotice, Field, Modal, useText } from "../common";
import { useInterfaceMotion, MOTION } from "../motion";
import { InfiniteCanvas } from "./viewport";
import { CanvasCard } from "./nodes";
import { TaskTray } from "./task-tray";
import { ServicesPanel } from "./services-panel";
import { useDocument } from "./use-document";
import {
	canConnect,
	freePosition,
	id,
	makeNode,
	runLabel,
	terminal,
	wire,
	type Asset,
	type CanvasNode,
	type Catalog,
	type Project,
	type Run,
	type Service,
	type Viewport,
} from "./types";
import "./style.css";
import { ServiceLifecycle } from "./service-lifecycle";
import { RunStatus } from "../creative/status";
import "../creative/style.css";

export default function CanvasPage() {
	const t = useText();
	const { data: projects, refresh } = useQuery<{
		items: { id: string; title: string; node_count: number }[];
	}>("/api/creative/projects");
	const { data: catalog, error: catalogError } = useQuery<Catalog>(
		"/api/creative/catalog",
		10000,
	);
	const { data: services, error: servicesError } = useQuery<{ items: Service[] }>(
		"/api/creative/services",
		2000,
	);
	const [identity, setIdentity] = useState<string | null>(null),
		[initial, setInitial] = useState<Project | null>(null);
	const [modelsOpen, setModelsOpen] = useState(false),
		[projectOpen, setProjectOpen] = useState(false),
		[loadError, setLoadError] = useState("");
	const select = useCallback((value: string) => {
		setIdentity(value);
		try {
			localStorage.setItem("onecat:last-canvas", value);
		} catch {
			/* optional */
		}
	}, []);
	useEffect(() => {
		if (identity || !projects?.items.length) return;
		let last: string | null = null;
		try {
			last = localStorage.getItem("onecat:last-canvas");
		} catch {
			/* optional */
		}
		select(
			projects.items.find((p) => p.id === last)?.id || projects.items[0].id,
		);
	}, [projects, identity, select]);
	useEffect(() => {
		if (!identity) return;
		const controller = new AbortController();
		setInitial(null);
		setLoadError("");
		void api<Project>(`/api/creative/projects/${identity}`, {
			signal: controller.signal,
		})
			.then((p) => {
				if (!controller.signal.aborted) setInitial(p);
			})
			.catch((error) => {
				if (!controller.signal.aborted) setLoadError(error.message);
			});
		return () => controller.abort();
	}, [identity]);
	const create = async (workflow?: string) => {
		const node = makeNode("generate");
		if (workflow) {
			node.workflow = workflow;
			if (workflow.startsWith("image-"))
				node.parameters = { ...node.parameters, width: 1024, height: 1024 };
		}
		node.service_id =
			services?.items.find((s) => s.workflows.includes(node.workflow))?.id ||
			"";
		const p = await mutation<Project>("/api/creative/projects", {
			title: t("新的创作", "New creation"),
			nodes: workflow ? [node] : [],
		});
		select(p.id);
		setProjectOpen(false);
		refresh();
	};
	return (
		<section className="oc-creative-page">
			{(catalogError || servicesError) && <ErrorNotice error={catalogError || servicesError} />}
			{initial && catalog ? (
				<CanvasEditor
					key={initial.id}
					initial={initial}
					catalog={catalog}
					services={services?.items || []}
					openModels={() => setModelsOpen(true)}
					openProjects={() => setProjectOpen(true)}
					onNewProject={select}
				/>
			) : (
				<div className="oc-canvas-welcome">
					<div className="oc-canvas-welcome-mark">
						<Sparkles size={30} />
					</div>
					<span className="oc-canvas-eyebrow">1CAT CREATIVE</span>
					<h1>{t("让想法在画布上延续", "Give your ideas room to grow")}</h1>
					<p>
						{t(
							"把文字、图片和视频连接起来。每一次生成，都是下一次创作的起点。",
							"Connect text, images and video. Every result is a starting point for what comes next.",
						)}
					</p>
					<ErrorNotice error={loadError} />
					{identity && !loadError ? (
						<LoaderCircle className="animate-spin" />
					) : (
						<div className="oc-canvas-starters">
							<Action variant="outline" run={() => create("image-text")}>
								<ImageIcon />
								{t("从一张图片开始", "Start with an image")}
							</Action>
							<Action variant="outline" run={() => create("h3-t2va")}>
								<Film />
								{t("创作一段视频", "Create a video")}
							</Action>
							<Action variant="ghost" run={() => create()}>
								<Plus />
								{t("空白画布", "Blank canvas")}
							</Action>
						</div>
					)}
					<Button variant="ghost" onClick={() => setModelsOpen(true)}>
						<Settings2 size={15} />
						{t("配置创作模型", "Configure creative models")}
					</Button>
				</div>
			)}
			{modelsOpen && catalog && (
				<ServicesPanel
					open
					onClose={() => setModelsOpen(false)}
					catalog={catalog}
					services={services?.items || []}
				/>
			)}
			<Modal
				open={projectOpen}
				onOpenChange={setProjectOpen}
				title={t("我的画布", "My canvases")}
			>
				<Action run={() => create()}>
					<Plus />
					{t("新建画布", "New canvas")}
				</Action>
				<div className="oc-canvas-projects">
					{projects?.items.map((p) => (
						<Button
							key={p.id}
							variant="outline"
							onClick={() => {
								select(p.id);
								setProjectOpen(false);
							}}
						>
							<FolderOpen />
							{p.title}
							<small>
								{p.node_count} {t("节点", "nodes")}
							</small>
						</Button>
					))}
				</div>
			</Modal>
		</section>
	);
}

function CanvasEditor({
	initial,
	catalog,
	services,
	openModels,
	openProjects,
	onNewProject,
}: {
	initial: Project;
	catalog: Catalog;
	services: Service[];
	openModels: () => void;
	openProjects: () => void;
	onNewProject: (id: string) => void;
}) {
	const t = useText();
	const { enabled } = useInterfaceMotion();
	const doc = useDocument(initial),
		{ project, edit, save } = doc;
	const projectRef = useRef(project);
	projectRef.current = project;
	const container = useRef<HTMLDivElement>(null),
		upload = useRef<HTMLInputElement>(null);
	const [selection, setSelection] = useState<string | null>(
		initial.nodes.find((n) => n.kind === "generate")?.id || null,
	);
	const [tool, setTool] = useState<"select" | "pan">("select"),
		[connecting, setConnecting] = useState<string | null>(null);
	const [pendingGeneration, setPendingGeneration] = useState<{ id: string; key: string; impacts: { id: string; name: string }[] } | null>(null);
	const [uploading, setUploading] = useState(false),
		[expandedAsset, setExpandedAsset] = useState<Asset | null>(null);
	const [assets, setAssets] = useState<Record<string, Asset>>(() =>
		Object.fromEntries((initial.assets || []).map((a) => [a.id, a])),
	);
	const [drag, setDrag] = useState<{ id: string; x: number; y: number } | null>(
		null,
	);
	const dragCleanup = useRef<() => void>(() => {});
	const [draft, setDraft] = useState<Record<string, unknown> | null>(() => {
		try {
			const value = localStorage.getItem(`onecat:canvas-draft:${initial.id}`);
			return value ? JSON.parse(value) : null;
		} catch {
			return null;
		}
	});
	const { data: runData, error: runError } = useQuery<{ items: Run[] }>(
		`/api/creative/projects/${initial.id}/runs`,
		1500,
	);
	const runs = runData?.items || [];
	const selected = project.nodes.find((n) => n.id === selection);
	const activeService = services.find((s) => s.id === selected?.service_id);
	const workflow = catalog.workflows.find((w) => w.id === selected?.workflow);
	const updateNode = (patch: Partial<CanvasNode>) =>
		edit((p) => ({
			...p,
			nodes: p.nodes.map((n) => (n.id === selection ? { ...n, ...patch } : n)),
		}));
	const rememberAssets = useCallback(
		(items: Asset[]) =>
			setAssets((old) => {
				const unseen = items.filter((a) => !old[a.id]);
				return unseen.length
					? { ...old, ...Object.fromEntries(unseen.map((a) => [a.id, a])) }
					: old;
			}),
		[],
	);
	const placeResult = useCallback(
		(run: Run, automatic = false) => {
			rememberAssets(run.assets);
			edit((p) => {
				if (
					automatic &&
					(p.placed_runs.includes(run.id) ||
						!p.nodes.some((n) => n.id === run.node_id))
				)
					return p;
				const origin = p.nodes.find((n) => n.id === run.node_id) || run.origin;
				const nodes: CanvasNode[] = [];
				for (const a of run.assets) {
					const spot = freePosition(
						[...p.nodes, ...nodes],
						origin.x + 350,
						origin.y,
					);
					nodes.push({
						...makeNode(a.kind, spot.x, spot.y),
						asset_id: a.id,
						title: a.name,
						origin_run: run.id,
					});
				}
				if (p.nodes.length + nodes.length > 300) return p;
				const edges = p.nodes.some((n) => n.id === run.node_id)
					? nodes.map((n) => ({ id: id(), source: run.node_id, target: n.id }))
					: [];
				return {
					...p,
					nodes: [...p.nodes, ...nodes],
					edges: [...p.edges, ...edges],
					placed_runs: [...new Set([...p.placed_runs, run.id])],
				};
			});
		},
		[edit, rememberAssets],
	);
	useEffect(() => {
		for (const run of runData?.items || [])
			if (run.state === "completed" && run.assets.length)
				placeResult(run, true);
	}, [runData, placeResult]);
	useEffect(() => () => dragCleanup.current(), []);
	const onViewportChange = useCallback(
		(viewport: Viewport) => edit((p) => ({ ...p, viewport }), false),
		[edit],
	);
	const onDeselect = useCallback(() => {
		setSelection(null);
		setConnecting(null);
	}, []);
	const onSelect = useCallback((id: string) => setSelection(id), []);
	const locate = (nodeId: string) => {
		const node = project.nodes.find((n) => n.id === nodeId);
		if (!node) return;
		setSelection(node.id);
		onViewportChange({ x: 70 - node.x, y: 70 - node.y, k: 1 });
	};
	const fit = () => {
		if (!container.current || !project.nodes.length) {
			onViewportChange({ x: 60, y: 60, k: 1 });
			return;
		}
		const left = Math.min(...project.nodes.map((n) => n.x)),
			top = Math.min(...project.nodes.map((n) => n.y));
		const right = Math.max(...project.nodes.map((n) => n.x + 280)),
			bottom = Math.max(...project.nodes.map((n) => n.y + 240));
		const { width, height } = container.current.getBoundingClientRect();
		const k = Math.max(
			0.1,
			Math.min(
				1,
				(width - 100) / (right - left),
				(height - 150) / (bottom - top),
			),
		);
		onViewportChange({
			x: (width - (right - left) * k) / 2 - left * k,
			y: 50 - top * k,
			k,
		});
	};
	const connect = useCallback(
		(target: string) => {
			if (!connecting) {
				setConnecting(target);
				return;
			}
			if (connecting === target) {
				setConnecting(null);
				return;
			}
			edit((p) => {
				const selectedKind = p.nodes.find((n) => n.id === connecting)?.kind;
				const source = selectedKind === "generate" ? target : connecting,
					end = selectedKind === "generate" ? connecting : target;
				if (!canConnect(p.nodes, p.edges, source, end)) {
					toast.error(
						t(
							"请选择一个素材和一个生成节点，不能形成循环引用。",
							"Connect a reference asset to a generation node without cycles.",
						),
					);
					return p;
				}
				return { ...p, edges: [...p.edges, { id: id(), source, target: end }] };
			});
			setConnecting(null);
		},
		[connecting, edit, t],
	);
	const dragNode = useCallback(
		(nodeId: string, event: React.PointerEvent<HTMLElement>) => {
			if (event.button !== 0) return;
			event.stopPropagation();
			event.preventDefault();
			container.current?.focus({ preventScroll: true });
			setSelection(nodeId);
			const node = projectRef.current.nodes.find((n) => n.id === nodeId);
			if (!node) return;
			dragCleanup.current();
			const start = { x: event.clientX, y: event.clientY },
				k = projectRef.current.viewport.k;
			let last = { id: nodeId, x: node.x, y: node.y },
				frame = 0;
			const move = (e: PointerEvent) => {
				last = {
					id: nodeId,
					x: Math.min(
						100000,
						Math.max(-100000, node.x + (e.clientX - start.x) / k),
					),
					y: Math.min(
						100000,
						Math.max(-100000, node.y + (e.clientY - start.y) / k),
					),
				};
				if (!frame)
					frame = requestAnimationFrame(() => {
						frame = 0;
						setDrag(last);
					});
			};
			const stop = () => {
				cancelAnimationFrame(frame);
				window.removeEventListener("pointermove", move);
				window.removeEventListener("pointerup", finish);
				window.removeEventListener("pointercancel", cancel);
				window.removeEventListener("blur", cancel);
			};
			const finish = () => {
				stop();
				setDrag(null);
				if (last.x !== node.x || last.y !== node.y)
					edit((p) => ({
						...p,
						nodes: p.nodes.map((n) =>
							n.id === nodeId ? { ...n, x: last.x, y: last.y } : n,
						),
					}));
			};
			const cancel = () => {
				stop();
				setDrag(null);
			};
			window.addEventListener("pointermove", move);
			window.addEventListener("pointerup", finish);
			window.addEventListener("pointercancel", cancel);
			window.addEventListener("blur", cancel);
			dragCleanup.current = stop;
		},
		[edit],
	);
	const add = (kind: CanvasNode["kind"], workflowId?: string) => {
		if (project.nodes.length >= 300) {
			toast.error(
				t("每张画布最多 300 个节点", "A canvas supports up to 300 nodes"),
			);
			return;
		}
		const viewport = project.viewport;
		const anchor = project.nodes.find((n) => n.id === selection);
		const spot = freePosition(
			project.nodes,
			anchor
				? anchor.x + (anchor.kind === "generate" ? 0 : 350)
				: (100 - viewport.x) / viewport.k,
			anchor
				? anchor.y + (anchor.kind === "generate" ? 280 : 0)
				: (100 - viewport.y) / viewport.k,
		);
		const node = makeNode(kind, spot.x, spot.y);
		if (workflowId) {
			node.workflow = workflowId;
			if (workflowId.startsWith("image"))
				node.parameters = { ...node.parameters, width: 1024, height: 1024 };
		}
		node.service_id =
			services.find((s) => s.workflows.includes(node.workflow))?.id || "";
		edit((p) => ({ ...p, nodes: [...p.nodes, node] }));
		setSelection(node.id);
	};
	const uploadFiles = async (files: File[]) => {
		if (uploading || !files.length) return;
		if (
			files.length > 12 ||
			files.length + projectRef.current.nodes.length > 300
		) {
			toast.error(
				t("每次最多上传 12 个素材", "Upload up to 12 assets at a time"),
			);
			return;
		}
		setUploading(true);
		try {
			for (const file of files) {
				const body = new FormData();
				body.append("file", file);
				const asset = await api<Asset>("/api/creative/assets", {
					method: "POST",
					body,
				});
				rememberAssets([asset]);
				const v = projectRef.current.viewport;
				const spot = freePosition(
					projectRef.current.nodes,
					(60 - v.x) / v.k,
					(100 - v.y) / v.k,
				);
				const node = {
					...makeNode(asset.kind, spot.x, spot.y),
					asset_id: asset.id,
					title: file.name,
				};
				edit((p) => ({ ...p, nodes: [...p.nodes, node] }));
				setSelection(node.id);
			}
		} catch (error) {
			toast.error((error as Error).message);
		} finally {
			setUploading(false);
			if (upload.current) upload.current.value = "";
		}
	};
	const remove = () => {
		if (!selected) return;
		edit((p) => ({
			...p,
			nodes: p.nodes.filter((n) => n.id !== selected.id),
			edges: p.edges.filter(
				(e) => e.source !== selected.id && e.target !== selected.id,
			),
		}));
		setSelection(null);
		setConnecting(null);
	};
    const submitPrepared = async (value: { id: string; key: string }) => {
        await mutation("/api/creative/generations", { preparation_id: value.id, request_key: value.key, confirm_switch: true });
        setPendingGeneration(null); refreshData();
    };
    const run = async () => {
        if (!selected) return;
        const saved = await save();
        const payload = { node_id: selected.id, revision: saved.revision, request_key: id() };
        if (activeService?.kind === "h3-local" || activeService?.kind === "image-local") {
            const prepared = await mutation<{ id: string; impacts: { id: string; name: string }[] }>(`/api/creative/projects/${saved.id}/runs/prepare`, payload);
            const value = { ...prepared, key: payload.request_key };
            if (value.impacts.length) setPendingGeneration(value); else await submitPrepared(value);
        } else {
            await mutation(`/api/creative/projects/${saved.id}/runs`, payload); refreshData();
        }
    };

	const latest = selected && runs.find((r) => r.node_id === selected.id);
	const renderingNodes = drag
		? project.nodes.map((n) =>
				n.id === drag.id ? { ...n, x: drag.x, y: drag.y } : n,
			)
		: project.nodes;
	const nodeMap = new Map(renderingNodes.map((n) => [n.id, n]));
	const selectedAsset = selected?.asset_id
		? assets[selected.asset_id]
		: undefined;
	return (
		<>
			<header className="oc-canvas-header">
				<div>
					<Button
						variant="ghost"
						size="icon-sm"
						aria-label={t("我的画布", "My canvases")}
						onClick={async () => {
							try {
								await save();
								openProjects();
							} catch {}
						}}
					>
						<FolderOpen size={18} />
					</Button>
					<Input
						aria-label={t("画布名称", "Canvas title")}
						value={project.title}
						onChange={(e) => edit((p) => ({ ...p, title: e.target.value }))}
					/>
					<small aria-live="polite">
						{doc.saving ? (
							<LoaderCircle className="animate-spin" size={12} />
						) : !doc.dirty ? (
							<Check size={12} />
						) : null}
						{doc.error
							? t("未保存", "Unsaved")
							: doc.saving
								? t("保存中", "Saving")
								: doc.dirty
									? t("待保存", "Unsaved")
									: t("已保存", "Saved")}
					</small>
				</div>
				<Button variant="outline" onClick={openModels}>
					<Settings2 size={15} />
					{t("创作模型", "Creative models")}
				</Button>
			</header>
			{(doc.error || runError) && (
				<div className="oc-canvas-save-error">
					<ErrorNotice error={doc.error || runError} />
					{doc.error && (
						<>
							<Action variant="outline" run={save}>
								{t("重试保存", "Retry save")}
							</Action>
							<Action
								variant="ghost"
								run={async () => {
									const p = await mutation<Project>("/api/creative/projects", {
										...wire(project),
										revision: 0,
										title: project.title + t(" · 副本", " · Copy"),
									});
									onNewProject(p.id);
								}}
							>
								{t("保存为新画布", "Save as new canvas")}
							</Action>
						</>
					)}
				</div>
			)}
			{draft && (
				<div className="oc-canvas-draft">
					<span>
						{t("发现上次未保存的草稿", "An unsaved draft is available")}
					</span>
					<Action
						variant="ghost"
						run={async () => {
							const p = await mutation<Project>("/api/creative/projects", {
								...draft,
								revision: 0,
								title: t("恢复的创作", "Recovered creation"),
							});
							localStorage.removeItem(`onecat:canvas-draft:${initial.id}`);
							setDraft(null);
							onNewProject(p.id);
						}}
					>
						{t("恢复为新画布", "Recover as new canvas")}
					</Action>
					<Button
						variant="ghost"
						onClick={() => {
							localStorage.removeItem(`onecat:canvas-draft:${initial.id}`);
							setDraft(null);
						}}
					>
						{t("忽略", "Dismiss")}
					</Button>
				</div>
			)}
			<div
				className="oc-canvas-body"
				onPaste={(e) => {
					if ((e.target as Element).closest('input,textarea,[contenteditable],[role="dialog"],[role="alertdialog"]'))
						return;
					const files = Array.from(e.clipboardData.files);
					if (files.length) {
						e.preventDefault();
						void uploadFiles(files);
					}
				}}
				onKeyDown={(e) => {
					if (
						(e.target as Element).closest(
							'input,textarea,select,[contenteditable],[role="dialog"],[role="alertdialog"]',
						)
					)
						return;
					if (e.key === "Escape") {
						setConnecting(null);
						setSelection(null);
					}
					if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
						e.preventDefault();
						e.shiftKey ? doc.redo() : doc.undo();
					}
					if (e.key === "Delete" || e.key === "Backspace") {
						e.preventDefault();
						remove();
					}
				}}
			>
				<div className="oc-canvas-stage">
					<div className="oc-canvas-toolbar" data-canvas-no-zoom>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("选择", "Select")}
							aria-pressed={tool === "select"}
							onClick={() => setTool("select")}
						>
							<MousePointer2 size={17} />
						</Button>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("移动画布", "Pan canvas")}
							aria-pressed={tool === "pan"}
							onClick={() => setTool("pan")}
						>
							<Hand size={17} />
						</Button>
						<span />
						<Button variant="ghost" onClick={() => add("text")}>
							<FileText size={16} />
							{t("文字", "Text")}
						</Button>
						<Button
							variant="ghost"
							disabled={uploading}
							onClick={() => upload.current?.click()}
						>
							{uploading ? (
								<LoaderCircle size={16} className="animate-spin" />
							) : (
								<Upload size={16} />
							)}
							{t("素材", "Media")}
						</Button>
						<Button variant="ghost" onClick={() => add("generate")}>
							<Sparkles size={16} />
							{t("生成", "Generate")}
						</Button>
					</div>
					<InfiniteCanvas
						containerRef={container}
						viewport={project.viewport}
						tool={tool}
						onViewportChange={onViewportChange}
						onCanvasDeselect={onDeselect}
						onDrop={(e) => {
							e.preventDefault();
							void uploadFiles(Array.from(e.dataTransfer.files));
						}}
					>
						<svg className="oc-canvas-edges" aria-hidden="true">
							{project.edges.map((edge) => {
								const a = nodeMap.get(edge.source),
									b = nodeMap.get(edge.target);
								if (!a || !b) return null;
								const x1 = a.x + 280,
									y1 = a.y + 120,
									x2 = b.x,
									y2 = b.y + 120;
								return (
									<path
										key={edge.id}
										d={`M ${x1} ${y1} C ${x1 + 100} ${y1}, ${x2 - 100} ${y2}, ${x2} ${y2}`}
										className={
											selection === edge.target || selection === edge.source
												? "selected"
												: ""
										}
									/>
								);
							})}
						</svg>
						{renderingNodes.map((node) => (
							<CanvasCard
								key={node.id}
								node={node}
								asset={node.asset_id ? assets[node.asset_id] : undefined}
								selected={selection === node.id}
								connecting={connecting === node.id}
								onSelect={onSelect}
								onDrag={dragNode}
								onConnect={connect}
								run={runs.find((r) => r.node_id === node.id)}
							/>
						))}
					</InfiniteCanvas>
					{!project.nodes.length && (
						<div className="oc-canvas-empty" data-canvas-no-zoom>
							<Sparkles size={27} />
							<h2>{t("从一个想法开始", "Start with an idea")}</h2>
							<p>
								{t(
									"放入素材，连接生成节点，继续创作。",
									"Add references, connect a generation node, and create.",
								)}
							</p>
							<div>
								<Button
									variant="secondary"
									onClick={() => add("generate", "image-text")}
								>
									<ImageIcon size={16} />
									{t("生图", "Image")}
								</Button>
								<Button
									variant="secondary"
									onClick={() => add("generate", "h3-t2va")}
								>
									<Film size={16} />
									{t("生视频", "Video")}
								</Button>
							</div>
						</div>
					)}
					<input
						ref={upload}
						type="file"
						multiple
						accept="image/png,image/jpeg,image/webp,video/mp4,video/webm,audio/wav,audio/mpeg"
						hidden
						onChange={(e) => void uploadFiles(Array.from(e.target.files || []))}
					/>
					{connecting && (
						<div className="oc-canvas-connection-hint" role="status">
							<Link2 size={14} />
							{t(
								"点击另一节点的「输入」或「引用」完成连线",
								"Click Input or Use on the other node to connect",
							)}
							<Button
								size="icon-sm"
								variant="ghost"
								onClick={() => setConnecting(null)}
								aria-label={t("取消连线", "Cancel connection")}
							>
								<X size={14} />
							</Button>
						</div>
					)}
					<div className="oc-canvas-bottom-tools" data-canvas-no-zoom>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("撤销", "Undo")}
							disabled={!doc.canUndo}
							onClick={doc.undo}
						>
							<Undo2 size={15} />
						</Button>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("重做", "Redo")}
							disabled={!doc.canRedo}
							onClick={doc.redo}
						>
							<Redo2 size={15} />
						</Button>
						<span />
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("缩小", "Zoom out")}
							onClick={() =>
								onViewportChange({
									...project.viewport,
									k: Math.max(0.1, project.viewport.k / 1.2),
								})
							}
						>
							<Minus size={15} />
						</Button>
						<small>{Math.round(project.viewport.k * 100)}%</small>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("放大", "Zoom in")}
							onClick={() =>
								onViewportChange({
									...project.viewport,
									k: Math.min(3, project.viewport.k * 1.2),
								})
							}
						>
							<Plus size={15} />
						</Button>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("适应画布", "Fit canvas")}
							onClick={fit}
						>
							<Maximize size={15} />
						</Button>
					</div>
					<TaskTray
						runs={runs}
						onLocate={locate}
						onRestore={(r) => placeResult(r)}
					/>
				</div>
				<AnimatePresence initial={false}>
					{selected && (
						<motion.aside
							key="inspector"
							className="oc-canvas-inspector"
							initial={{ opacity: 0, x: enabled ? 12 : 0 }}
							animate={{ opacity: 1, x: 0 }}
							exit={{ opacity: 0, x: enabled ? 12 : 0 }}
							transition={{ duration: enabled ? MOTION.panel : 0 }}
						>
							<header>
								<div>
									<span className="oc-canvas-eyebrow">
										{selected.kind === "generate"
											? t("生成设置", "GENERATION")
											: t("素材详情", "REFERENCE")}
									</span>
									<h2>{selected.title || t("创作节点", "Creative node")}</h2>
								</div>
								<Button
									variant="ghost"
									size="icon-sm"
									aria-label={t("关闭参数", "Close inspector")}
									onClick={() => setSelection(null)}
								>
									<X size={17} />
								</Button>
							</header>
							<div className="oc-canvas-inspector-content">
								<Field label={t("名称", "Name")}>
									<Input
										value={selected.title}
										onChange={(e) => updateNode({ title: e.target.value })}
									/>
								</Field>
								{(selected.kind === "generate" || selected.kind === "text") && (
									<Field
										label={
											selected.kind === "text"
												? t("内容", "Text")
												: t("描述你想生成的画面", "Describe your creation")
										}
									>
										<CreativePrompt value={selected.text} onChange={text => updateNode({ text })} />
									</Field>
								)}
								{selected.kind === "generate" && (
									<>
										<Field label={t("工作流", "Workflow")}>
											<select
												value={selected.workflow}
												onChange={(e) => {
													const w = catalog.workflows.find(
														(w) => w.id === e.target.value,
													)!;
													updateNode({
														workflow: w.id,
														service_id: activeService?.workflows.includes(w.id)
															? activeService.id
															: services.find((s) => s.workflows.includes(w.id))
																	?.id || "",
														parameters: {
															...selected.parameters,
															width: w.output === "image" ? 1024 : 1344,
															height: w.output === "image" ? 1024 : 768,
														},
													});
												}}
											>
												{catalog.workflows.map((w) => (
													<option key={w.id} value={w.id}>
														{t(w.name, w.name_en)}
														{w.experimental
															? t(" · 实验性", " · Experimental")
															: ""}
													</option>
												))}
											</select>
										</Field>
										<Field
											label={t("模型服务", "Model service")}
											hint={t(
												"也可以先选模型，工作流会自动匹配。",
												"You can also choose a model first; the workflow will match it.",
											)}
										>
											<select
												value={selected.service_id}
												onChange={(e) => {
													const s = services.find(
														(s) => s.id === e.target.value,
													);
													const wid = s?.workflows.includes(selected.workflow)
														? selected.workflow
														: s?.workflows[0] || selected.workflow;
													updateNode({
														service_id: e.target.value,
														workflow: wid,
														parameters: {
															...selected.parameters,
															width: wid.startsWith("image-") ? 1024 : 1344,
															height: wid.startsWith("image-") ? 1024 : 768,
														},
													});
												}}
											>
												<option value="">
													{t("选择模型服务", "Choose model service")}
												</option>
												{services.map((s) => (
													<option key={s.id} value={s.id}>
														{s.name}
														{s.workflows.includes(selected.workflow)
															? ""
															: t(" · 将切换工作流", " · Switches workflow")}
													</option>
												))}
											</select>
										</Field>
										<button
											className="oc-canvas-text-button"
											onClick={openModels}
										>
											<Plus size={14} />
											{t("添加或管理模型", "Add or manage models")}
										</button>
										{workflow?.provider === "h3" && (
											<p className="oc-canvas-notice">
                                                {activeService?.kind === "h3-local"
                                                    ? t("生成时自动准备模型；需要切换服务时会先提示。", "The model is prepared automatically. Service switches are confirmed first.")
                                                    : t("使用所选模型服务生成，作品会保存在工作台。", "Your selected service generates the result, which is saved in the workbench.")}
											</p>
										)}
                    <div className="oc-creative-output-fields"><OutputFields kind={workflow?.output === "image" ? "image" : "video"} value={selected.parameters} onChange={value => updateNode({ parameters: { ...selected.parameters, ...value } })} model={catalog.models?.find(m => m.id === (activeService?.checkpoint || "h3"))} legacySizes={activeService?.output_sizes || (activeService?.kind === "image-api" ? [[1024,1024],[1536,1024],[1024,1536]] : undefined)} /></div>
                    {workflow?.partition === "fl2va" && workflow.min_images > 0 && <small>{t("首尾帧会按输出比例居中裁切，保留原图比例。", "Keyframes are center-cropped to the output aspect ratio, without stretching.")}</small>}
                    <div className="oc-canvas-input-list">
											<h3>{t("参考输入", "References")}</h3>
											<small>
												{workflow?.min_images === workflow?.max_images &&
												workflow?.max_images
													? t(
															`需要 ${workflow.max_images} 张图片；连线顺序对应首帧、尾帧。`,
															`Needs ${workflow.max_images} images; connection order determines first / last frame.`,
														)
													: t(
															"按连线顺序传入。结果素材也可以继续引用。",
															"References follow connection order. Generated assets can be reused.",
														)}
											</small>
											{project.edges
												.filter((e) => e.target === selected.id)
												.map((edge, index) => {
													const n = project.nodes.find(
														(n) => n.id === edge.source,
													)!;
													return (
														<div key={edge.id}>
															<span>
																{index + 1}.{" "}
																{n.title || n.text.slice(0, 20) || n.kind}
															</span>
															<Button
																variant="ghost"
																size="icon-sm"
																aria-label={t("移除连线", "Remove connection")}
																onClick={() =>
																	edit((p) => ({
																		...p,
																		edges: p.edges.filter(
																			(e) => e.id !== edge.id,
																		),
																	}))
																}
															>
																<X size={12} />
															</Button>
														</div>
													);
												})}
											<select
												aria-label={t("添加参考素材", "Add reference")}
												value=""
												onChange={(e) => {
													const source = e.target.value;
													if (!source) return;
													edit((p) =>
														canConnect(p.nodes, p.edges, source, selected.id)
															? {
																	...p,
																	edges: [
																		...p.edges,
																		{ id: id(), source, target: selected.id },
																	],
																}
															: p,
													);
												}}
											>
												<option value="">
													{t(
														"＋ 引用画布上的素材",
														"+ Reference a canvas asset",
													)}
												</option>
												{project.nodes
													.filter((n) =>
														canConnect(
															project.nodes,
															project.edges,
															n.id,
															selected.id,
														),
													)
													.map((n) => (
														<option key={n.id} value={n.id}>
															{n.title || n.text.slice(0, 30) || n.kind}
														</option>
													))}
											</select>
										</div>
										<details>
											<summary>{t("高级参数", "Advanced settings")}</summary>
											{workflow?.provider === "h3" && (
												<Field label={t("随机种子", "Seed")}>
													<Input
														type="number"
														min="0"
														max="2147483647"
														value={selected.parameters.seed}
														onChange={(e) =>
															updateNode({
																parameters: {
																	...selected.parameters,
																	seed: Number(e.target.value),
																},
															})
														}
													/>
												</Field>
											)}
											{workflow?.provider === "h3" && (
												<>
													<Field label={t("LoRA 强度", "LoRA strength")}>
														<Input
															type="number"
															min="0"
															max="2"
															step="0.1"
															value={selected.parameters.lora_scale}
															onChange={(e) =>
																updateNode({
																	parameters: {
																		...selected.parameters,
																		lora_scale: Number(e.target.value),
																	},
																})
															}
														/>
													</Field>
													<small>
														{t(
															"采样步数自动跟随已加载的 LoRA 配方，避免 Turbo 步数与 sigma 点混淆。",
															"Sampling follows the loaded LoRA recipe to avoid confusing denoising steps with sigma points.",
														)}
													</small>
												</>
											)}
											{workflow?.provider === "image-api" && (
												<small>
													{t(
														"图片接口使用服务自身的采样默认值。",
														"Image sampling uses the service defaults.",
													)}
												</small>
											)}
										</details>
									</>
								)}
								{selectedAsset && (
									<div className="oc-canvas-asset-detail">
										{selectedAsset.kind === "image" && (
											<img
												src={selectedAsset.thumbnail_url || selectedAsset.url}
												alt={selectedAsset.name}
											/>
										)}
										<Button
											variant="outline"
											onClick={() => setExpandedAsset(selectedAsset)}
										>
											<Maximize size={14} />
											{t("查看原始素材", "View original")}
										</Button>
										<a href={selectedAsset.url + "?download=true"} download>
											<Download size={14} />
											{t("下载素材", "Download asset")}
										</a>
									</div>
								)}
								<div className="oc-canvas-node-actions">
									<Button
										variant="ghost"
										onClick={() => {
											if (project.nodes.length >= 300) return;
											const node = {
												...selected,
												id: id(),
												...freePosition(
													project.nodes,
													selected.x + 350,
													selected.y,
												),
											};
											edit((p) => ({ ...p, nodes: [...p.nodes, node] }));
											setSelection(node.id);
										}}
									>
										<Copy size={14} />
										{t("复制节点", "Duplicate")}
									</Button>
									<Button variant="ghost" onClick={remove}>
										<Trash2 size={14} />
										{t("移除", "Remove")}
									</Button>
								</div>
							</div>
							{selected.kind === "generate" && (
								<footer className="oc-canvas-generate-footer">
                                    {latest && <RunStatus run={latest} now={Date.now()/1000} />}
                                    {latest && !terminal(latest.state) && <Action variant="outline" disabled={!!latest.cancel_requested || !!latest.cancel_note} run={async () => { await mutation(`/api/creative/runs/${latest.id}/cancel`); refreshData(); }}>{latest.cancel_note ? t("等待生成结束", "Finishing generation") : t("取消任务", "Cancel task")}</Action>}
                                    {activeService && <details><summary>{t("模型服务详情", "Model service details")}</summary><ServiceLifecycle key={activeService.id} service={activeService} /></details>}
                                    {activeService && (
										<Action
											disabled={
												!activeService || !!(latest && !terminal(latest.state))
											}
											run={run}
										>
											<Sparkles size={16} />
											{t("开始生成", "Generate")}
										</Action>
									)}
									<small>
										{t(
											"结果保存在服务器；离开页面不会中断任务。",
											"Results stay on the server. Leaving this page does not interrupt tasks.",
										)}
									</small>
								</footer>
							)}
						</motion.aside>
					)}
				</AnimatePresence>
			</div>
            <Modal open={!!pendingGeneration} onOpenChange={open => { if (!open) setPendingGeneration(null); }} title={t("切换创作模型", "Switch creative model")}>
                <p>{t("当前请求结束后，将释放以下服务并自动生成。", "After active requests finish, these services will be released and generation will continue.")}</p>
                <ul>{pendingGeneration?.impacts.map(effect => <li key={effect.id}>{effect.name}</li>)}</ul>
                <Action run={async () => { if (pendingGeneration) await submitPrepared(pendingGeneration); }}>{t("确认切换并生成", "Switch and generate")}</Action>
            </Modal>
			<Modal
				open={!!expandedAsset}
				onOpenChange={(open) => {
					if (!open) setExpandedAsset(null);
				}}
				title={expandedAsset?.name || t("素材", "Asset")}
			>
				{expandedAsset?.kind === "image" ? (
					<img
						className="oc-canvas-full-asset"
						src={expandedAsset.url}
						alt={expandedAsset.name}
					/>
				) : expandedAsset?.kind === "video" ? (
					<video
						className="oc-canvas-full-asset"
						src={expandedAsset.url}
						controls
						playsInline
					/>
				) : expandedAsset ? (
					<audio src={expandedAsset.url} controls />
				) : null}
			</Modal>
		</>
	);
}
