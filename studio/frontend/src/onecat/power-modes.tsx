// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useIsPresent } from "motion/react";
import {
	Leaf,
	Scale,
	Zap,
	Check,
	SlidersHorizontal,
	ChevronDown,
	Cpu,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/onecat/ui";
import {
	mutation,
	refreshData,
	useQuery,
	type GPU,
	type Engine,
	type Hardware,
	type Job,
} from "./api";
import {
	Action,
	ErrorNotice,
	Modal,
	NumberField,
	useText,
	format,
	jobLabel,
} from "./common";
import { Phase, Segments, useInterfaceMotion } from "./motion";
import { useBrowserState } from "./browser-state";
import { GPUControls } from "./gpu-controls";

import { LiveGPUs, type ModeChange } from "./live-gpus";
import { modelLabel } from "./model-label";

type Actual = {
	power_limit_w: number;
	graphics_clock_mhz: number | null;
	clock_policy_known: boolean;
	recovery_pending?: boolean;
};
type Device = GPU & {
	confirmed_mode?: string | null;
	authorized: boolean;
	external_processes: GPU["processes"];
	actual?: Actual;
	policy?: { setting: Hardware };
};
type ControlStatus = {
	recent_changes?: ModeChange[];
    setup_required?: boolean;
	scope: "all" | "custom";
	active: string | null;
	state: string;
	error?: string;
	restore_error?: string;
	gpu_uuids: string[];
	devices: Device[];
	pending?: {
		mode: string | null;
		job: string;
		stage: string;
        detail?: string;
        created_at?: number;
		gpu_uuids: string[];
		cancel_requested?: boolean;
	};
	items: {
		id: string;
		available: boolean;
		reason?: string;
		setting: Hardware;
		settings?: Record<string, Hardware>;
		boost_clocks_mhz?: Record<string, number>;
	}[];
	last_result?: {
		id: string;
		state: string;
		error?: string;
		result?: {
			devices?: Record<
				string,
				{ state: string; error?: string; restored_exact?: boolean }
			>;
		};
	};
};

type PowerTarget = {
	mode: string | null;
	gpu_uuids?: string[];
	job?: string;
};

// Keep the measured content in flow through exit, including its outer spacing.
function PowerReveal({ children }: { children: ReactNode }) {
	const { enabled } = useInterfaceMotion();
	const present = useIsPresent();
	const content = useRef<HTMLDivElement>(null);
	const [height, setHeight] = useState(0);
	useLayoutEffect(() => {
		const el = content.current!;
		const measure = () => setHeight(el.getBoundingClientRect().height);
		measure();
		const observer = new ResizeObserver(measure);
		observer.observe(el);
		return () => observer.disconnect();
	}, []);
	const ease = [0.38, 0, 0.22, 1] as const;
	return (
		<motion.div className="perf-reveal" inert={!present || undefined} aria-hidden={!present || undefined}
			initial={{ height: 0, opacity: 0 }}
			animate={{ height, opacity: 1 }}
			exit={{ height: 0, opacity: 0 }}
			transition={{
				height: { duration: enabled ? (present ? 0.46 : 0.38) : 0, ease },
				opacity: { duration: enabled ? 0.28 : 0, ease },
			}}>
			<div ref={content} className="perf-reveal-content">{children}</div>
		</motion.div>
	);
}

export function PowerModes() {
	const t = useText();
	const { data: engine } = useQuery<Engine>("/api/inference/status", 2000);
	const [scope, setScope] = useBrowserState("onecat:power-scope", "all", true);
	const [selected, setSelected] = useBrowserState<string[]>(
		"onecat:power-gpus",
		[],
		true,
	);
	const query =
		scope === "custom"
			? selected.length
				? "?" +
					selected.map((u) => `gpu_uuids=${encodeURIComponent(u)}`).join("&")
				: "?gpu_uuids="
			: "";
	const { data, error, refresh } = useQuery<ControlStatus>(
		"/api/gpu/power-modes" + query,
		2000,
	);
	const [localWaiting, setWaiting] = useState<PowerTarget | null>(null);
	const [confirmed, setConfirmed] = useState<PowerTarget | null>(null);
	const notified = useRef("");
	const lastTarget = useRef<PowerTarget | null>(null);
	const pending = data?.pending;
	const waiting = localWaiting || (pending?.job !== notified.current ? pending : undefined);
	const displayTarget = waiting || confirmed;
	const showingConfirmation = !!confirmed && !waiting;
	const { data: jobs } = useQuery<{ items: Job[] }>(
		waiting?.job ? "/api/jobs" : null,
		1000,
	);
	const job = jobs?.items.find((j) => j.id === waiting?.job);
	const submitting = useRef(false);
	const [scopeOpen, setScopeOpen] = useState(false);
	const [taskOpen, setTaskOpen] = useState(false);
	const scopeTrigger = useRef<HTMLButtonElement>(null);
	const manualTrigger = useRef<HTMLButtonElement>(null);
	const taskTrigger = useRef<HTMLButtonElement>(null);
	function openTask(event: React.MouseEvent<HTMLButtonElement>) {
		taskTrigger.current = event.currentTarget;
		setTaskOpen(true);
	}
	const [manual, setManual] = useState(false),
		[watts, setWatts] = useState(185),
		[clock, setClock] = useState(0),
		[manualError, setManualError] = useState("");
	const uuids = data?.gpu_uuids || [];
	const empty = scope === "custom" && selected.length === 0;
	const scopeReady =
		data?.scope === scope &&
		(scope === "all" ||
			(selected.length === uuids.length &&
				selected.every((u) => uuids.includes(u))));
	const canSubmit =
		scopeReady &&
		!error &&
		!data?.error &&
		uuids.length > 0 &&
		!empty &&
		!waiting;
	useEffect(() => {
		if (waiting?.job) lastTarget.current = waiting;
	}, [waiting]);

	// Either poll can deliver completion first, including jobs started in another tab.
	const result = job && ["completed", "failed", "cancelled"].includes(job.state) ? job : data?.last_result;
	useEffect(() => {
		const target = lastTarget.current;
		if (!result || !target || result.id !== target.job || notified.current === result.id ||
			!["completed", "failed", "cancelled"].includes(result.state)) return;
		notified.current = result.id;
		if (result.state === "completed") {
			setConfirmed(target);
			toast.success(t("GPU 设置已应用并确认", "GPU settings applied and confirmed"));
		} else if (result.state === "failed") {
			toast.error(result.error || t("设置未完成", "Settings could not be applied"));
		} else {
			toast.message(t("已取消等待", "Waiting cancelled"));
		}
		setWaiting(null);
		refreshData();
	}, [result?.id, result?.state, waiting?.job]);
	useEffect(() => {
		if (!confirmed) return;
		const timer = window.setTimeout(() => setConfirmed(null), 580);
		return () => window.clearTimeout(timer);
	}, [confirmed]);
	async function submit(mode: string | null, setting?: Hardware) {
		if (submitting.current || !canSubmit) return;
		submitting.current = true;
		const target = [...uuids];
		setConfirmed(null);
		setWaiting({ mode, gpu_uuids: target });
		try {
			const created = await mutation<Job>(
				mode ? "/api/gpu/power-mode" : "/api/gpu/settings",
				mode ? { id: mode, gpu_uuids: target } : { setting, gpu_uuids: target },
			);
			setWaiting({ mode, job: created.id, gpu_uuids: target });
			refresh();
			setManual(false);
		} catch (error) {
			setWaiting(null);
			setManualError((error as Error).message);
			throw error;
		} finally {
			submitting.current = false;
		}
	}
	const performance = data?.items.find((item) => item.id === "performance");
	const performanceSettings = Object.values(performance?.settings || {});
	const performanceValues = (key: "power_limit_w" | "graphics_clock_mhz") =>
		[...new Set((key === "graphics_clock_mhz"
			? Object.values(performance?.boost_clocks_mhz || {})
			: performanceSettings.map((setting) => setting[key])).filter((value) => value != null))]
			.sort((a, b) => a! - b!).join(" / ");
	const modes = [
		{
			id: "eco",
			icon: Leaf,
			title: t("省电模式", "Power saver"),
			watts: "150",
			subtitle: t("W / 卡 · 功率上限", "W / GPU · power limit"),
			description: t("日常对话，低功耗运行。", "Lower draw for everyday chat."),
			detail: t(
				"975 MHz · 既有实测目标 110–120 W / 卡",
				"975 MHz · Prior measured target 110–120 W / GPU",
			),
		},
		{
			id: "balanced",
			icon: Scale,
			title: t("均衡模式", "Balanced"),
			watts: "185",
			subtitle: t("W / 卡 · 功率上限", "W / GPU · power limit"),
			description: t(
				"兼顾响应速度与能耗。",
				"Balance response speed and power.",
			),
			detail: t("180–190 W 档位 · 动态频率", "180–190 W tier · dynamic clocks"),
		},
		{
			id: "performance",
			icon: Zap,
			title: t("性能模式", "Performance"),
			watts: performanceValues("power_limit_w") || "—",
			subtitle: t("W / 卡 · 功率上限", "W / GPU · power limit"),
			description: t(
				`动态最高 ${performanceValues("graphics_clock_mhz") || "—"} MHz · 闲置自动降频。`,
				`Dynamic up to ${performanceValues("graphics_clock_mhz") || "—"} MHz · Lower clocks when idle.`,
			),
			detail: t(
				`解除固定锁频 · 负载时允许升至 ${performanceValues("graphics_clock_mhz") || "—"} MHz`,
				`Reset fixed clock locks · Allow boost up to ${performanceValues("graphics_clock_mhz") || "—"} MHz under load`,
			),
		},
	];
	const devices = [...(data?.devices || [])].sort((a, b) => a.index - b.index);
	const scopeDevices = devices.filter((d) => uuids.includes(d.uuid));
	const actualKnown = scopeReady && !error && !data?.error;
	const activeMode = actualKnown
		? modes.find((m) => m.id === data?.active)
		: undefined;
	const actualSignatures = new Set(
		scopeDevices.map(
			(d) => `${d.actual?.power_limit_w}:${d.actual?.graphics_clock_mhz}`,
		),
	);
	const currentLabel = !scopeReady
		? t("正在同步", "Syncing")
		: activeMode?.title ||
			(actualKnown &&
			scopeDevices.length === uuids.length &&
			uuids.length > 0 &&
			scopeDevices.every((d) => d.actual?.clock_policy_known)
				? actualSignatures.size > 1
					? t("混合配置", "Mixed settings")
					: t("自定义", "Custom")
				: data?.error ? t("控制暂不可用", "Controls unavailable") : !uuids.length ? t("未选择设备", "No devices selected") : t("尚无确认读数", "No confirmed settings"));
	const stage = job?.stage || data?.pending?.stage || "queued";
	const targetLabel =
		modes.find((m) => m.id === displayTarget?.mode)?.title ||
		t("手动设置", "Manual settings");
	const failure =
		data?.last_result?.state === "failed" ? data.last_result : undefined;
	const recoveryDevices = devices.filter((d) => d.actual?.recovery_pending);
	const hasTaskDetails = !!(
		waiting ||
		failure ||
		data?.restore_error ||
		recoveryDevices.length
	);
	const notice = [
		...new Set(
			[
				empty
					? t(
							"请至少选择一张已授权 GPU。",
							"Select at least one authorized GPU.",
						)
					: error || data?.error,
				data?.restore_error,
				recoveryDevices.length
					? t(
							"部分 GPU 设置尚待恢复，请查看任务详情。",
							"Some GPU settings still need recovery. See task details.",
						)
					: "",
				failure?.error ||
					(failure
						? t(
								"上次设置失败，请查看任务详情。",
								"Last change failed. See task details.",
							)
						: ""),
			].filter(Boolean),
		),
	].join("\n");
	const missing =
		scope === "custom" &&
		!!data &&
		selected.some((u) => !devices.some((d) => d.uuid === u));
	const selectedCount =
		scope === "custom"
			? selected.length
			: devices.filter((d) => d.authorized).length;
	const scopeLabel = !data
		? t("正在读取 GPU", "Reading GPUs")
		: scope === "all"
			? t(`全部已授权 ${selectedCount} 张 GPU`, `All ${selectedCount} authorized GPUs`)
			: t(`已选 ${selectedCount} 张 GPU`, `${selectedCount} GPUs selected`);
	const pendingTargets = localWaiting?.gpu_uuids || data?.pending?.gpu_uuids || [];
    const cancelling = !!data?.pending?.cancel_requested;
    const applying = stage === "applying_gpu_settings" && !cancelling;
    const stageMessage = cancelling ? t("正在取消等待", "Cancelling queued change")
        : applying ? t("正在应用设置并核对显卡读数", "Applying settings and verifying GPU readings")
        : stage === "recovering_gpu_settings" ? t("正在恢复先前设置", "Restoring previous settings")
        : stage === "waiting_for_engine" || stage === "waiting_for_requests"
            ? data?.pending?.detail || jobLabel(stage, t)
            : waiting?.job ? t("已提交，等待执行", "Queued for application") : t("正在提交设置", "Submitting settings");
	const shownUuids = showingConfirmation ? confirmed.gpu_uuids || [] : pendingTargets;
	const targetDevices = devices.filter((d) => shownUuids.includes(d.uuid));
	const targetNames = [
		...targetDevices.map((d) => `GPU ${d.index}`),
		...pendingTargets.filter((u) => !devices.some((d) => d.uuid === u)),
	];
	const externalDevices = devices.filter((d) => d.external_processes.length);
	const cancelDisabled =
		!!data?.pending?.cancel_requested ||
		["applying_gpu_settings", "recovering_gpu_settings"].includes(
			data?.pending?.stage || stage,
		);
	return (
        <div className="oc-performance">
          <section className="perf-controls">
            <header className="perf-controls-head">
              <div className="perf-model">
                <span className="perf-chip"><Cpu size={20}/></span>
                <div><h2>{engine?.state === "ready" ? modelLabel(engine.profile?.name || engine.profile?.served_model_name || "") : t("GPU 功耗控制", "GPU power controls")}</h2>
                <button ref={scopeTrigger} type="button" className="perf-link" onClick={() => setScopeOpen(true)} aria-label={t("更改 GPU 控制范围", "Change GPU control scope")}>{scopeLabel} <ChevronDown size={13}/></button></div>
              </div>
              <Segments className="perf-switch" role="group" aria-label={t("功耗模式", "Power mode")}>
                {modes.map(mode => {
                  const option = data?.items.find(item => item.id === mode.id);
                  const selectedMode = displayTarget?.mode || activeMode?.id;
                  return <button key={mode.id} type="button" aria-label={mode.title}
                    data-switching={waiting?.mode === mode.id || undefined}
                    aria-pressed={selectedMode === mode.id} disabled={!canSubmit || !option?.available}
                    title={option?.reason || `${mode.watts} W / GPU · ${mode.description}`}
                    onClick={() => void submit(mode.id).catch(e => toast.error((e as Error).message))}>
                    <mode.icon size={16}/>{mode.title}
                  </button>;
                })}
              </Segments>
            </header>
			<div className="oc-power-status">
				<AnimatePresence>
					{displayTarget && <PowerReveal key="power-transition">
						<div className="perf-transition oc-power-target" data-applying={applying}
							data-confirmed={showingConfirmation} data-mode={displayTarget.mode} data-cancelling={cancelling}>
							<span className="perf-indicator" aria-hidden="true">
								<span className="perf-wave"><i/><i/><i/><i/><i/></span>
								<Check size={24} className="perf-confirm-icon"/>
							</span>
							<div className="perf-transition-copy" role="status">
								<strong>{showingConfirmation ? targetLabel : t(`切换至${targetLabel}`, `Switching to ${targetLabel}`)}</strong>
								<small><Phase phase={showingConfirmation ? "confirmed" : stageMessage}>
									{showingConfirmation ? t("设置已应用并确认", "Settings applied and confirmed") : stageMessage}
								</Phase></small>
							</div>
							{targetDevices.length > 0 && <div className="perf-transition-boards">
								{targetDevices.map(d => {
									const verified = showingConfirmation || (applying && actualKnown && !!displayTarget.mode && d.confirmed_mode === displayTarget.mode);
									const label = verified ? t("读数已确认", "Readback confirmed") : t("等待确认", "Awaiting confirmation");
									return <span key={d.uuid} data-verified={verified} aria-label={`GPU ${d.index} · ${label}`} title={label}>
										{verified ? <Check size={11} aria-hidden="true"/> : <i aria-hidden="true"/>}GPU {d.index}
									</span>;
								})}
							</div>}
							<div className="oc-power-status-actions perf-pending-actions" inert={showingConfirmation || undefined} aria-hidden={showingConfirmation || undefined}>
								<Action variant="ghost" disabled={!pending || cancelDisabled || showingConfirmation}
									run={() => mutation(`/api/jobs/${pending!.job}/cancel`)}>
									{cancelling ? t("取消中", "Cancelling") : t("取消等待", "Cancel waiting")}
								</Action>
								<Button variant="ghost" onClick={openTask}>{t("任务详情", "Task details")}</Button>
							</div>
						</div>
					</PowerReveal>}
				</AnimatePresence>
				<div className="perf-status-base">
					<div role="status" className="oc-power-status-text">
						<span>
							{t("当前：", "Current: ")}
							<b>{currentLabel}</b>
                        {activeMode && <small className="perf-readback"> · {activeMode.watts} W / GPU · {t("已确认", "Verified")} {scopeDevices.filter(d => d.confirmed_mode === activeMode.id).length}/{uuids.length}</small>}
						</span>
					</div>
					<div className="oc-power-status-actions">
                    <Button ref={manualTrigger} variant="ghost" disabled={!canSubmit}
                      onClick={() => { setManualError(""); setManual(true); }}><SlidersHorizontal size={15}/>{t("手动调节", "Manual controls")}</Button>
						{!waiting && hasTaskDetails &&
							<Button variant="ghost" onClick={openTask}>{t("任务详情", "Task details")}</Button>}
					</div>
				</div>
			</div>
			{data?.setup_required ? <GPUControls compact /> : <ErrorNotice error={notice} />}
			<details className="oc-power-notes">
				<summary>
					<ChevronDown size={14} />
					{t("档位说明", "Mode details")}
				</summary>
				<div className="oc-power-notes-body">
					<p>
						{t(
							"显示的是每卡功率上限，实际功率随负载变化。模型启动与切换会保留你的选择。",
							"Values are per-GPU power limits; actual draw varies with workload. Model launches and changes preserve your choice.",
						)}
					</p>
					{modes.map((mode) => {
						const item = data?.items.find((i) => i.id === mode.id);
						return (
							<div key={mode.id} className="oc-power-note">
								<strong>{mode.title}</strong>
								<span>{mode.detail}</span>
								{item?.reason && (
									<span className="oc-status-warn">{item.reason}</span>
								)}
							</div>
						);
					})}
					<p>
						{t(
							"外部程序占用的已授权 GPU 也会调节，程序不会被停止。涉及 Studio 请求时等待请求结束；启动、停止或校准期间排队。首次调节会建立动态频率基线。",
							"Authorized GPUs running external workloads are included without stopping their processes. Overlapping Studio requests drain first; launch, stop and calibration operations finish before queued changes. First use establishes a dynamic clock baseline.",
						)}
					</p>
				</div>
			</details>
			<Modal
				open={scopeOpen}
				returnFocusRef={scopeTrigger}
				onOpenChange={setScopeOpen}
				title={t("GPU 控制范围", "GPU control scope")}
				description={t(
					"选择调节哪些显卡；关闭窗口后点击档位才会应用设置。",
					"Choose which GPUs to control. Settings apply only when you select a power mode or apply manual controls.",
				)}
				footer={
					<>
						<span className="oc-muted">{scopeLabel}</span>
						<Button onClick={() => setScopeOpen(false)}>
							{t("完成", "Done")}
						</Button>
					</>
				}
			>
				<Segments
					className="oc-segments"
					role="group"
					aria-label={t("GPU 控制范围", "GPU control scope")}
				>
					<button
						aria-pressed={scope === "all"}
						onClick={() => setScope("all")}
					>
						{t("全部已授权 GPU", "All authorized GPUs")}
					</button>
					<button
						aria-pressed={scope === "custom"}
						onClick={() => setScope("custom")}
					>
						{t("自选 GPU", "Choose GPUs")}
					</button>
				</Segments>
				<div className="oc-power-devices">
					{devices.map((device) => (
						<label className="oc-power-device" key={device.uuid}>
							{scope === "custom" && (
								<input
									type="checkbox"
									checked={selected.includes(device.uuid)}
									disabled={!device.authorized}
									aria-label={`GPU ${device.index}`}
									onChange={(event) => {
										const checked = event.currentTarget.checked;
										setSelected((values) =>
											checked
												? [...new Set([...values, device.uuid])]
												: values.filter((u) => u !== device.uuid),
										);
									}}
								/>
							)}
							<div className="oc-power-device-info">
								<strong>GPU {device.index}</strong>
								<small>
									{device.actual?.clock_policy_known
										? `${format(device.actual.power_limit_w, 0)} W · ${device.actual.graphics_clock_mhz ? `${device.actual.graphics_clock_mhz} MHz` : t("动态频率", "Dynamic clocks")}`
										: `${format(device.power_limit_w, 0)} W · ${t("锁频状态未知", "Clock policy unknown")}`}
								</small>
							</div>
							<div className="oc-power-device-state">
								<span>
									{device.authorized
										? t("已授权", "Authorized")
										: t("未授权", "Unauthorized")}
								</span>
								{!!device.external_processes.length && (
									<small>{t("外部程序占用", "External workload")}</small>
								)}
								{device.actual?.recovery_pending && (
									<small className="oc-status-warn">
										{t("待恢复", "Recovery required")}
									</small>
								)}
							</div>
						</label>
					))}
				</div>
				{missing && (
					<Button
						variant="ghost"
						onClick={() =>
							setSelected((values) =>
								values.filter((u) => devices.some((d) => d.uuid === u)),
							)
						}
					>
						{t("移除已不存在的 GPU", "Remove missing GPUs")}
					</Button>
				)}
				<ErrorNotice
					error={
						empty
							? t(
									"请至少选择一张已授权 GPU。",
									"Select at least one authorized GPU.",
								)
							: error || data?.error
					}
				/>
				{!!externalDevices.length && (
					<details className="oc-power-occupancy">
						<summary>
							<ChevronDown size={14} />
							{t("占用详情", "Workload details")} · {externalDevices.length} GPU
						</summary>
						<p className="oc-muted">
							{t(
								"所选卡上的外部程序也受功耗设置影响，程序不会被停止。",
								"Power settings also affect external workloads on selected GPUs without stopping them.",
							)}
						</p>
						{externalDevices.map((device) => (
							<p key={device.uuid}>
								GPU {device.index} ·{" "}
								{device.external_processes
									.map((p) => `${p.name} (PID ${p.pid})`)
									.join(", ")}
							</p>
						))}
					</details>
				)}
			</Modal>
			<Modal
				open={taskOpen}
				returnFocusRef={hasTaskDetails ? taskTrigger : scopeTrigger}
				onOpenChange={setTaskOpen}
				title={t("任务详情", "Task details")}
				description={t(
					"显示固定的任务范围与逐卡结果。更改选卡不会改变已提交的任务。",
					"Shows the submitted scope and per-GPU results. Changing your selection does not change a submitted task.",
				)}
				footer={
					<Button onClick={() => setTaskOpen(false)}>
						{t("完成", "Done")}
					</Button>
				}
			>
				{waiting ? (
					<div className="oc-power-task-details">
						<p>
							{t("目标：", "Target: ")}
							{targetLabel}
						</p>
						<p>{jobLabel(stage, t)}</p>
						<p>{targetNames.join(", ") || t("正在提交", "Submitting")}</p>
					</div>
				) : (
					<p>{t("没有等待中的任务", "No pending tasks")}</p>
				)}
				<ErrorNotice error={data?.restore_error || failure?.error} />
				{!!recoveryDevices.length && (
					<p className="oc-status-warn">
						{t("待恢复：", "Recovery required: ")}
						{recoveryDevices.map((d) => `GPU ${d.index}`).join(", ")}
					</p>
				)}
				{failure && (
					<div className="oc-power-result">
						{Object.entries(failure.result?.devices || {}).map(
							([uuid, result]) => (
								<p key={uuid}>
									GPU {devices.find((d) => d.uuid === uuid)?.index ?? uuid} ·{" "}
									{result.state === "restored"
										? result.restored_exact === false
											? t(
													"已恢复原功率上限与动态频率；原锁频未知",
													"Original power restored with dynamic clocks; previous lock was unknown",
												)
											: t("已恢复原设置", "Original settings restored")
										: result.state === "unchanged"
											? t("未改动", "Unchanged")
											: t("待恢复", "Recovery required")}{" "}
									{result.error}
								</p>
							),
						)}
						<Action
							variant="outline"
							disabled={!!waiting}
							run={async () => {
								const created = await mutation<Job>(
									`/api/jobs/${failure.id}/retry`,
								);
								setConfirmed(null);
								setWaiting({ mode: null, job: created.id, gpu_uuids: failure.result?.devices ? Object.keys(failure.result.devices) : undefined });
							}}
						>
							{t("重试", "Retry")}
						</Action>
					</div>
				)}
			</Modal>
			<Modal
				open={manual}
				returnFocusRef={canSubmit ? manualTrigger : scopeTrigger}
				onOpenChange={setManual}
				title={t("GPU 功率与频率", "GPU power and clock controls")}
				description={t(
					`应用到当前选定的 ${uuids.length} 张 GPU，与快捷档位范围一致。`,
					`Applies to the same ${uuids.length} selected GPUs as power presets.`,
				)}
			>
				<div className="oc-form-grid">
					<NumberField
						label={t("每卡功率上限 W", "Power limit per GPU (W)")}
						value={watts}
						onChange={setWatts}
						min={1}
						max={2000}
					/>
					<NumberField
						label={t(
							"锁定频率 MHz（0 为动态）",
							"Graphics clock MHz (0 = dynamic)",
						)}
						value={clock}
						onChange={setClock}
						min={0}
						max={5000}
					/>
				</div>
				<ErrorNotice error={manualError} />
				<Action
					disabled={!canSubmit}
					run={() =>
						submit(null, {
							power_limit_w: watts,
							graphics_clock_mhz: clock || null,
							reset_clocks: !clock,
						})
					}
				>
					{t("应用设置", "Apply settings")}
				</Action>
			</Modal>
          </section>
          <LiveGPUs changes={data?.recent_changes || []} devices={devices} pending={waiting ? {mode: waiting.mode, uuids: pendingTargets} : undefined}/>
        </div>
	);
}
