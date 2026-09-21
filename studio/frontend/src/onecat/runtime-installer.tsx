// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useRef } from "react";
import { Link } from "@tanstack/react-router";
import {
	Check,
	ChevronDown,
	Download,
	ExternalLink,
	LoaderCircle,
	RefreshCw,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Switch } from "@/onecat/ui";
import { api, mutation, refreshData, useQuery, type Job } from "./api";
import { Action, ErrorNotice, Field, bytes, jobLabel, useText } from "./common";
import { useBrowserState } from "./browser-state";
import { DownloadRate } from "./download-rate";
import { Phase } from "./motion";

type Release = {
	id: string;
	version: string;
	title: string;
	tag: string;
	python: string | null;
	cuda: string | null;
	torch: string | null;
	platform: string;
	bytes: number;
	evidence: string;
	prerelease: boolean;
	available: boolean;
	reasons: string[];
	installed_runtime_id: string | null;
	assets: { name: string; bytes: number; sha256: string | null }[];
};
type Releases = {
	items: Release[];
	checked_at: number | null;
	stale: boolean;
	error?: string;
	source: string;
	repository_url: string;
};
const terminal = (job: Job) =>
	["completed", "failed", "cancelled"].includes(job.state);

export function RuntimeInstaller() {
	const t = useText();
	const { data, error, refresh } = useQuery<Releases>(
		"/api/runtimes/releases",
		30000,
	);
	const { data: tasks } = useQuery<{ items: Job[] }>("/api/jobs", 2000);
	const [selected, setSelected] = useBrowserState(
		"onecat:runtime-release",
		"",
		true,
	);
	const [experimental, setExperimental] = useBrowserState(
		"onecat:runtime-prereleases",
		false,
		true,
	);
	const items = data?.items.filter((r) => experimental || !r.prerelease) || [];
	const chosenId =
		selected ||
		items.find((r) => r.available && !r.prerelease)?.id ||
		items[0]?.id;
	const release = items.find((r) => r.id === chosenId);
	const matchingJobs = (tasks?.items || [])
		.filter(
			(j) => j.kind === "install_runtime" && j.runtime_release_id === chosenId,
		)
		.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
	const job = matchingJobs.find((j) => !terminal(j)) || matchingJobs[0];
	const busy = !!job && !terminal(job);
	const otherJobs =
		tasks?.items.filter(
			(j) =>
				j.kind === "install_runtime" &&
				!terminal(j) &&
				j.runtime_release_id !== chosenId,
		) || [];
	const completed = useRef("");
	useEffect(() => {
		if (job && terminal(job) && completed.current !== job.id) {
			completed.current = job.id;
			refreshData();
		}
	}, [job?.id, job?.state]);
	const unavailable = release?.reasons.join("; ");
	const missing = !!data && !!selected && !release;
	return (
		<div className="oc-panel oc-runtime-installer">
			<div className="oc-row">
				<div>
					<h2>
						{t("从 GitHub Releases 安装", "Install from GitHub Releases")}
					</h2>
					<p className="oc-muted">1CatAI / 1Cat-vLLM</p>
				</div>
				<Action
					variant="ghost"
					run={async () => {
						const updated = await api<Releases>(
							"/api/runtimes/releases?refresh=true",
						);
						refresh();
						if (updated.error) throw new Error(updated.error);
					}}
				>
					<RefreshCw />
					{t("检查更新", "Check for updates")}
				</Action>
			</div>
			<ErrorNotice error={error || data?.error} />
			{data?.stale && (
				<p className="oc-status-warn">
					{data.source === "bundled"
						? t(
								"当前显示内置备用版本，恢复连接后可获取完整发布列表。",
								"Showing bundled releases. Reconnect to load the full release list.",
							)
						: t(
								"当前显示上次成功获取的发布列表。",
								"Showing the last successfully fetched release list.",
							)}
				</p>
			)}
			{!data ? (
				<p className="oc-runtime-loading" role="status">
					<LoaderCircle className="animate-spin" size={16} />
					{t("正在读取发布列表…", "Loading releases…")}
				</p>
			) : (
				<>
					<div className="oc-runtime-choice">
						<Field label={t("发行版本", "Release version")}>
							<select
								value={chosenId || ""}
								onChange={(e) => setSelected(e.target.value)}
							>
								{missing && (
									<option value={selected}>
										{t("请重新选择发行包", "Select an available release")}
									</option>
								)}
								{!items.length && (
									<option value="">
										{t("没有可用的发行包", "No release wheels available")}
									</option>
								)}
								{items.map((r) => (
									<option key={r.id} value={r.id}>
										{r.tag} · Python {r.python || "—"}
										{r.prerelease ? t(" · 实验版", " · Experimental") : ""}
										{r.installed_runtime_id
											? t(" · 已安装", " · Installed")
											: ""}
									</option>
								))}
							</select>
						</Field>
						<label className="oc-toggle-row">
							<Switch
								checked={experimental}
								onCheckedChange={(value) => {
									setExperimental(value);
									if (!value && release?.prerelease) setSelected("");
								}}
							/>
							{t("包含实验版", "Include experimental releases")}
						</label>
					</div>
					<ErrorNotice
						error={
							missing
								? t(
										"当前列表中找不到所选发行包，请重新选择或检查更新。",
										"The selected release is absent from this list. Choose a release or check for updates.",
									)
								: unavailable
						}
					/>
					{release && (
						<>
							<div className="oc-runtime-summary">
								<strong>1Cat-vLLM {release.version}</strong>
								{release.prerelease && (
									<span className="oc-status-warn">
										{t("实验版", "Experimental")}
									</span>
								)}
								<p className="oc-muted">
									Python {release.python || "—"} ·{" "}
									{release.torch ? `PyTorch ${release.torch} · ` : ""}CUDA{" "}
									{release.cuda || "—"} · {release.platform}
								</p>
								<p className="oc-muted">
									{t(
										"V100 / SM70 · 独立环境",
										"V100 / SM70 · Isolated runtime",
									)}{" "}
									· {release.assets.length} wheel
									{release.bytes > 0 ? ` · ${bytes(release.bytes)}` : ""}
								</p>
							</div>
							<div className="oc-runtime-install-stage" aria-live="polite">
								{job && (
									<Phase
										phase={`${job.id}:${job.state}:${job.stage}`}
										className="oc-runtime-job"
									>
										<span className="oc-runtime-job-title">
											{busy ? (
												<LoaderCircle size={16} className="animate-spin" />
											) : job.state === "completed" ? (
												<Check size={16} />
											) : null}
											{job.cancel_requested && busy
												? t("正在取消安装…", "Cancelling installation…")
												: jobLabel(busy ? job.stage : job.state, t)}
										</span>
										{job.asset_name &&
											busy &&
											["downloading", "downloading_runtime"].includes(
												job.stage,
											) && (
												<>
													<span className="oc-runtime-asset-name">
														{job.asset_index} / {job.asset_count} ·{" "}
														{job.asset_name}
													</span>
													{job.stage === "downloading" && (
														<>
															<progress
																max={job.expected_bytes || undefined}
																value={
																	job.expected_bytes
																		? job.downloaded_bytes || 0
																		: undefined
																}
																aria-label={t(
																	"当前 wheel 下载进度",
																	"Current wheel download progress",
																)}
															/>
															<span className="oc-runtime-transfer">
																<span>
																	{bytes(job.downloaded_bytes || 0)} /{" "}
																	{job.expected_bytes
																		? bytes(job.expected_bytes)
																		: "—"}
																</span>
																<DownloadRate job={job} />
															</span>
														</>
													)}
												</>
											)}
									</Phase>
								)}
								<ErrorNotice
									error={job?.state === "failed" ? job.error : undefined}
								/>
							</div>
							<div
								key={release.id}
								className="oc-actions oc-runtime-install-actions"
							>
								{busy ? (
									<Action
										variant="outline"
										disabled={job?.cancel_requested}
										run={() => mutation(`/api/jobs/${job!.id}/cancel`)}
									>
										{t("取消安装", "Cancel installation")}
									</Action>
								) : release.installed_runtime_id ? (
									<Button disabled>
										<Check />
										{t("已安装此发行包", "Release installed")}
									</Button>
								) : job && ["failed", "cancelled"].includes(job.state) ? (
									<Action
										disabled={!release.available}
										run={() => mutation(`/api/jobs/${job.id}/retry`)}
									>
										<RefreshCw />
										{t("重试安装", "Retry installation")}
									</Action>
								) : (
									<Action
										disabled={!release.available || !!error}
										run={() => {
											setSelected(release.id);
											return mutation("/api/runtimes/install", {
												release_id: release.id,
											});
										}}
									>
										<Download />
										{t("安装独立环境", "Install isolated runtime")}
									</Action>
								)}
								{job && (
									<Button variant="ghost" asChild>
										<Link to="/service">{t("任务与日志", "Tasks & logs")}</Link>
									</Button>
								)}
								{release.installed_runtime_id && (
									<Button variant="outline" asChild>
										<Link to="/models">
											{t("配置模型", "Configure a model")}
										</Link>
									</Button>
								)}
								<Button variant="ghost" asChild>
									<a href={release.evidence} target="_blank" rel="noreferrer">
										<ExternalLink />
										{t("发布说明", "Release notes")}
									</a>
								</Button>
							</div>
							<p className="oc-muted oc-runtime-hint">
								{t(
									"安装或升级会创建独立环境，保留已有环境。完成后在模型启动预设中选择新环境才会生效。",
									"Installs and upgrades create an isolated environment and preserve existing ones. Select the new runtime in a model launch profile to use it.",
								)}
							</p>
							<details className="oc-runtime-assets">
								<summary>
									<ChevronDown size={14} />
									{t("安装文件与校验", "Files & verification")}
								</summary>
								<p className="oc-muted">
									{t(
										"至少预留 20 GiB 磁盘。配套 wheel 一同安装，PyTorch 版本以 wheel 内的精确依赖为准。",
										"Reserve at least 20 GiB. Companion wheels install together; the wheel metadata determines the exact PyTorch version.",
									)}
								</p>
								{release.assets.map((a) => (
									<div key={a.name}>
										<strong>{a.name}</strong>
										<small>
											{a.bytes > 0 ? bytes(a.bytes) + " · " : ""}SHA256:{" "}
											{a.sha256 || t("缺失", "Missing")}
										</small>
									</div>
								))}
							</details>
						</>
					)}
				</>
			)}
			{otherJobs.length > 0 && (
				<p className="oc-muted">
					{t(
						`后台还有 ${otherJobs.length} 个版本正在安装，可在服务页查看。`,
						`${otherJobs.length} other releases are installing. Follow them on the Service page.`,
					)}
				</p>
			)}
			{data?.checked_at && (
				<p className="oc-muted oc-runtime-checked">
					{t("发布列表更新于", "Release list checked")}{" "}
					{new Date(data.checked_at * 1000).toLocaleString()}
				</p>
			)}
		</div>
	);
}
