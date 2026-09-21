// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useState } from "react";
import {
	LoaderCircle,
	ChevronUp,
	X,
	Download,
	CircleCheck,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Action, useText } from "../common";
import { mutation } from "../api";
import { terminal, runLabel, type Run } from "./types";
import { RunStatus } from "../creative/status";

export function TaskTray({
	runs,
	onLocate,
	onRestore,
}: {
	runs: Run[];
	onLocate: (nodeId: string) => void;
	onRestore: (run: Run) => void;
}) {
	const t = useText();
	const [open, setOpen] = useState(false),
		[now, setNow] = useState(Date.now());
	const active = runs.filter((r) => !terminal(r.state));
	useEffect(() => {
		if (!active.length) return;
		const timer = setInterval(() => setNow(Date.now()), 1000);
		return () => clearInterval(timer);
	}, [active.length]);
	if (!runs.length) return null;
	return (
		<div
			className={`oc-canvas-task-tray ${open ? "open" : ""}`}
			data-canvas-no-zoom
		>
			<button
				className="oc-canvas-task-toggle"
				onClick={() => setOpen((v) => !v)}
				aria-expanded={open}
			>
				{active.length ? (
					<LoaderCircle size={15} className="animate-spin" />
				) : (
					<CircleCheck size={15} />
				)}
				{active.length
					? t(
							`${active.length} 个创作任务进行中`,
							`${active.length} creative tasks in progress`,
						)
					: t("创作记录", "Creation history")}
				<span>{runs.length}</span>
				<ChevronUp size={14} />
			</button>
			{open && (
				<div className="oc-canvas-task-list">
					{runs.map((run) => (
						<article key={run.id}>
							<header>
								<button onClick={() => onLocate(run.node_id)}>
									{run.model}
								</button>
								<small>
									{runLabel(terminal(run.state) ? run.state : run.stage, t)}
								</small>
							</header>
							<p>{run.prompt}</p>
                            <RunStatus run={run} now={now/1000} />

							{run.telemetry?.source === "selected_gpu_boards" && (
								<small>
									{t(
										"所选 GPU 板卡实测，可能包含其他负载",
										"Measured selected GPU boards; may include other workloads",
									)}
								</small>
							)}
							{run.state === "failed" && run.error && (
								<p className="oc-canvas-inline-error" role="alert">
									{run.error}
								</p>
							)}
							{run.cancel_note && (
								<p className="oc-muted">
									{t(
										"后端暂不支持中途停止，完成后将保留结果。",
										"Backend cannot interrupt this generation; its result will be retained.",
									)}
								</p>
							)}
							<div className="oc-actions">
								{!terminal(run.state) && (
									<Action
										variant="ghost"
										disabled={run.cancel_requested}
										run={() => mutation(`/api/creative/runs/${run.id}/cancel`)}
									>
										<X size={14} />
										{run.cancel_requested
											? t("等待结束", "Waiting for completion")
											: t("取消任务", "Cancel task")}
									</Action>
								)}
								{run.resumable && (
									<Action
										variant="outline"
										run={() => mutation(`/api/creative/runs/${run.id}/resume`)}
									>
										{t("恢复跟踪原任务", "Resume original task")}
									</Action>
								)}
								{run.assets.length > 0 && (
									<>
										<Button variant="ghost" onClick={() => onRestore(run)}>
											{t("放回画布", "Add to canvas")}
										</Button>
										<a href={run.assets[0].url + "?download=true"} download>
											<Download size={14} />
											{t("下载", "Download")}
										</a>
									</>
								)}
							</div>
						</article>
					))}
				</div>
			)}
		</div>
	);
}
