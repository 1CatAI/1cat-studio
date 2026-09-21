// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { memo } from "react";
import {
	FileText,
	Film,
	Image as ImageIcon,
	Music2,
	Sparkles,
	ArrowRight,
	LoaderCircle,
} from "lucide-react";
import { useText } from "../common";
import {
	runLabel,
	terminal,
	type Asset,
	type CanvasNode,
	type Run,
} from "./types";

export const CanvasCard = memo(function CanvasCard({
	node,
	asset,
	selected,
	connecting,
	onSelect,
	onDrag,
	onConnect,
	run,
}: {
	node: CanvasNode;
	asset?: Asset;
	selected: boolean;
	connecting: boolean;
	onSelect: (id: string) => void;
	onDrag: (id: string, event: React.PointerEvent<HTMLElement>) => void;
	onConnect: (id: string) => void;
	run?: Run;
}) {
	const t = useText();
	const Icon = {
		text: FileText,
		image: ImageIcon,
		video: Film,
		audio: Music2,
		generate: Sparkles,
	}[node.kind];
	const title =
		node.title ||
		(node.kind === "generate"
			? t("生成节点", "Generation")
			: node.kind === "text"
				? t("文字", "Text")
				: asset?.name || t("素材", "Asset"));
	return (
		<article
			data-node-id={node.id}
			className={`oc-canvas-node ${selected ? "selected" : ""}`}
			style={{ transform: `translate(${node.x}px, ${node.y}px)` }}
			onPointerDown={() => onSelect(node.id)}
		>
			<header onPointerDown={(e) => onDrag(node.id, e)}>
				<Icon size={15} />
				<button type="button" onClick={() => onSelect(node.id)}>
					{title}
				</button>
			</header>
			<div className="oc-canvas-node-content" data-canvas-no-zoom>
				{node.kind === "text" && (
					<p>
						{node.text ||
							t(
								"在右侧写下想法或提示词…",
								"Write an idea or prompt in the inspector…",
							)}
					</p>
				)}
				{node.kind === "generate" && (
					<>
						<span className="oc-canvas-node-eyebrow">
							{node.workflow.startsWith("h3") ? "H3 · " : ""}
							{node.workflow.startsWith("h3")
								? t("视频生成", "Video generation")
								: t("图片生成", "Image generation")}
						</span>
						<p>
							{node.text ||
								t(
									"连接参考素材，描述你想创作的画面。",
									"Connect references and describe what you want to create.",
								)}
						</p>
					</>
				)}
				{node.kind === "image" && asset && (
					<img
						src={asset.thumbnail_url || asset.url}
						alt={title}
						loading="lazy"
						decoding="async"
						draggable={false}
					/>
				)}
				{node.kind === "video" && asset && (
					<video
						src={asset.url}
						controls
						preload="metadata"
						playsInline
						aria-label={title}
					/>
				)}
				{node.kind === "audio" && asset && (
					<audio
						src={asset.url}
						controls
						preload="metadata"
						aria-label={title}
					/>
				)}
			</div>
			<footer>
				<small>
					{node.kind === "generate" ? (
						run ? (
							<>
								{!terminal(run.state) && (
									<LoaderCircle size={12} className="animate-spin" />
								)}
								{runLabel(terminal(run.state) ? run.state : run.stage, t)}
							</>
						) : (
							t("准备创作", "Ready to configure")
						)
					) : asset ? (
						[
							asset.width && `${asset.width} × ${asset.height}`,
							asset.duration && `${asset.duration.toFixed(1)} s`,
						]
							.filter(Boolean)
							.join(" · ")
					) : (
						t("提示词素材", "Prompt reference")
					)}
				</small>
				<button
					className={connecting ? "active" : ""}
					aria-label={
						node.kind === "generate"
							? t("连接到此生成节点", "Connect to this generation")
							: t("引用这个素材", "Reference this asset")
					}
					onClick={(e) => {
						e.stopPropagation();
						onConnect(node.id);
					}}
				>
					<ArrowRight size={14} />
					{node.kind === "generate" ? t("输入", "Input") : t("引用", "Use")}
				</button>
			</footer>
		</article>
	);
});
