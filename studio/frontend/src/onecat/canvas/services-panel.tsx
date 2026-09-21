// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import {
	Boxes,
	Download,
	Plus,
	Settings2,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { Action, ErrorNotice, Field, Modal, bytes, useText } from "../common";
import { mutation, useQuery, type GPU, type Job } from "../api";
import type { Catalog, Service } from "./types";
import { runLabel } from "./types";
import { ServiceLifecycle } from "./service-lifecycle";

const blank = {
	name: "H3 · FL2VA",
	kind: "h3-local",
  checkpoint: "",
	model: "",
	partition: "fl2va",
	base_url: "http://127.0.0.1:8001",
	runtime_id: "",
	gpu_uuids: [] as string[],
	transformer_path: "",
	lora_path: "",
	attention_backend: "FLASH_ATTN_V100",
	image_edit: false,
};
export function ServicesPanel({
	open,
	onClose,
	catalog,
	services,
}: {
	open: boolean;
	onClose: () => void;
	catalog: Catalog;
	services: Service[];
}) {
	const t = useText();
	const { data: hardware } = useQuery<{ gpus: GPU[] }>("/api/gpu", 5000);
	const { data: jobs } = useQuery<{ items: Job[] }>("/api/jobs", 1500);
	const [editing, setEditing] = useState(false),
		[form, setForm] = useState(blank);
	const [editId, setEditId] = useState("");
	const [key, setKey] = useState<string | undefined>();
  const [presetRuntime, setPresetRuntime] = useState("");
  const [presetNotice, setPresetNotice] = useState("");
  const compatibleRuntimes = catalog.runtimes.filter(r => r.h3_supported);
  const selectedRuntime = compatibleRuntimes.find(r => r.id === presetRuntime)?.id || compatibleRuntimes[0]?.id || "";
	const local = form.kind === "h3-local" || form.kind === "image-local";
	const change = (field: string, value: unknown) =>
		setForm((p) => ({ ...p, [field]: value }));
	const installed = (identity: string) =>
		catalog.components.find((c) => c.id === identity)?.installed?.path || "";
	return (
		<Modal
			open={open}
			onOpenChange={(value) => {
				if (!value) onClose();
			}}
			title={t("创作模型与组件", "Creative models & components")}
		>
			<p className="oc-muted">
				{t(
					"加载会自动释放占用相同 GPU 的 Studio 模型。正在生成的任务请先完成或取消；外部程序单独提示。",
					"Loading releases Studio models on the selected GPUs. Finish or cancel active generation first; external workloads are identified separately.",
				)}
			</p>
            {!editing && !!catalog.presets?.length && <section className="oc-h3-presets">
              <h3>{t("H3 启动预设", "H3 launch presets")}</h3>
              <p className="oc-muted">{t("选择用途即可创建四卡预设，自动关联本地组件。创建后点击加载；GPU 被占用时会说明原因。H3 当前为开发适配。", "Choose a workflow to create a four-GPU preset using local components. Then select Load; occupied GPUs are reported. H3 is a development integration.")}</p>
              {!compatibleRuntimes.length && <Button variant="outline" size="sm" asChild><Link to="/setup">{t("准备 H3 运行环境", "Prepare an H3 runtime")}</Link></Button>}
              {compatibleRuntimes.length > 0 && <Field label={t("运行环境", "Runtime")}><select value={selectedRuntime} onChange={e => setPresetRuntime(e.target.value)}>{compatibleRuntimes.map(r => <option value={r.id} key={r.id}>{r.name}</option>)}</select></Field>}
              <div className="oc-h3-preset-grid">{catalog.presets.map(p => <div className="oc-h3-preset" key={p.id}>
                <strong>{t(p.name, p.name_en)}</strong>
                <p>{p.turbo ? t("使用对应版本的加速组件，输出规格随工作流选择", "Matching accelerator; choose output size in the workflow") : t("基础质量 · 默认采样", "Base quality · Default sampling")}</p>
                {p.missing_components.length > 0 && <span className="oc-muted">{t(`还需下载 ${p.missing_components.length} 个组件`, `${p.missing_components.length} components to download`)}</span>}
                {!p.hardware_available && <span className="oc-muted">{t("需要 4 张 V100 32 GB", "Requires 4 × V100 32 GB")}</span>}
                {!p.runtime_available && <span className="oc-muted">{t("需要支持 H3 的源码环境", "Requires an H3 source runtime")}</span>}
                <div className="oc-actions">
                  {p.missing_components.length > 0 ? <Action variant="outline" run={async () => { for (const id of p.missing_components) await mutation(`/api/creative/components/${id}/download`); setPresetNotice(t("组件下载已提交，可在下方查看进度。", "Downloads queued. Track them below.")); }}>{t("下载所需组件", "Download components")}</Action> : <Action disabled={!selectedRuntime || !p.hardware_available} run={async () => { const service = await mutation<Service>(`/api/creative/presets/${p.id}`, { runtime_id: selectedRuntime }); setPresetNotice(t(`预设已准备：${service.name}，点击加载即可使用。`, `Preset ready: ${service.name}. Select Load to use it.`)); }}>{t("创建预设", "Create preset")}</Action>}
                </div>
              </div>)}</div>
              {presetNotice && <p role="status">{presetNotice}</p>}
            </section>}
			<div className="oc-canvas-service-list" hidden={!services.length}>
				{services.map((s) => (
					<div className="oc-canvas-service-row" key={s.id}>
						<div>
							<strong>{s.name}</strong>
							<small>
								{s.kind === "image-api"
									? t("图片 API", "Image API")
                  : s.kind === "image-local" ? s.checkpoint || "Z-Image"
									: `H3 · ${s.partition.toUpperCase()}`}{" "}
								· {runLabel(s.instance.state, t)}
							</small>
						</div>
						<div className="oc-actions">
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label={t("编辑服务", "Edit service")}
								onClick={() => {
									setForm(
										Object.fromEntries(
											Object.keys(blank).map((k) => [
												k,
												(s as unknown as Record<string, unknown>)[k],
											]),
										) as typeof blank,
									);
									setEditId(s.id);
									setKey(undefined);
									setEditing(true);
								}}
							>
								<Settings2 size={16} />
							</Button>
                        </div>
                        <ServiceLifecycle service={s} />
					</div>
				))}
			</div>
			{!editing ? (
				<Button
					variant="outline"
					onClick={() => {
						setForm(blank);
						setEditId("");
						setKey(undefined);
						setEditing(true);
					}}
				>
					<Plus />
					{t("自定义模型服务", "Custom model service")}
				</Button>
			) : (
				<form
					className="oc-canvas-service-form"
					onSubmit={(e) => e.preventDefault()}
				>
					<h3>
						{editId
							? t("编辑模型服务", "Edit model service")
							: t("添加模型服务", "Add model service")}
					</h3>
					<div className="oc-form-grid">
						<Field label={t("服务类型", "Service type")}>
							<select
								value={form.kind}
								onChange={(e) => change("kind", e.target.value)}
							>
								<option value="h3-local">
									{t("本机 1Cat H3", "Local 1Cat H3")}
								</option>
								<option value="h3-api">
									{t("已有 H3 服务", "Existing H3 service")}
								</option>
								<option value="image-local">{t("本机 Z-Image", "Local Z-Image")}</option>
                <option value="image-api">
									{t("兼容图片 API", "Compatible image API")}
								</option>
							</select>
						</Field>
						<Field label={t("显示名称", "Display name")}>
							<Input
								value={form.name}
								onChange={(e) => change("name", e.target.value)}
							/>
						</Field>
						{form.kind.startsWith("h3-") && (
							<Field
								label={t("权重分区", "Weight partition")}
								hint={t(
									"FL2VA：文字、首尾帧；Ref2VA：多素材参考。更换分区需要重新加载服务。",
									"FL2VA: text/keyframes. Ref2VA: references. Changing partition requires loading a different service.",
								)}
							>
								<select
									value={form.partition}
									onChange={(e) => change("partition", e.target.value)}
								>
									<option value="fl2va">FL2VA</option>
									<option value="ref2va">Ref2VA</option>
								</select>
							</Field>
						)}
						{form.kind === "image-local" && <Field label={t("检查点", "Checkpoint")}><select value={form.checkpoint} onChange={e=>change("checkpoint",e.target.value)}><option value="">{t("选择模型", "Choose model")}</option><option value="z-image-turbo">Z-Image Turbo</option><option value="z-image">Z-Image</option></select></Field>}
            {local ? (
							<>
								<Field label={t("运行环境", "Runtime")}>
									<select
										value={form.runtime_id}
										onChange={(e) => change("runtime_id", e.target.value)}
									>
										<option value="">
											{t("选择支持 H3 的环境", "Choose an H3 environment")}
										</option>
										{catalog.runtimes.map((r) => (
											<option
												key={r.id}
												value={r.id}
												disabled={form.kind === "image-local" ? !r.image_supported : !r.h3_supported}
											>
												{r.name}
												{!(form.kind === "image-local" ? r.image_supported : r.h3_supported)
													? t(" · 尚无 H3 接口", " · No H3 API")
													: ""}
											</option>
										))}
									</select>
								</Field>
								<Field
									label={t("H3 本地组件目录", "Local H3 component directory")}
								>
									<Input
										value={form.model}
										onChange={(e) => change("model", e.target.value)}
										placeholder="/path/to/MiniMax-H3"
									/>
								</Field>
								{form.kind === "h3-local" && <><Field label={t("Transformer 权重", "Transformer checkpoint")}>
									<Input
										value={form.transformer_path}
										onChange={(e) => change("transformer_path", e.target.value)}
										placeholder=".safetensors"
									/>
								</Field>
								<Field label={t("LoRA（可选）", "LoRA (optional)")}>
									<Input
										value={form.lora_path}
										onChange={(e) => change("lora_path", e.target.value)}
										placeholder={t(
											"留空使用基础模型",
											"Leave blank for base model",
										)}
									/>
								</Field>
								<Field label={t("注意力后端", "Attention backend")}>
									<select
										value={form.attention_backend}
										onChange={(e) =>
											change("attention_backend", e.target.value)
										}
									>
										{["FLASH_ATTN_V100", "FLASHINFER_SM70", "TORCH_SDPA", "FASTVIDEO_VSA"].map(
											(v) => (
												<option key={v}>{v}</option>
											),
										)}
									</select>
								</Field>
								</>}<fieldset className="oc-canvas-gpu-picker">
									<legend>{t("使用 GPU", "Use GPUs")}</legend>
									{hardware?.gpus.map((g) => (
										<label key={g.uuid}>
											<input
												type="checkbox"
												checked={form.gpu_uuids.includes(g.uuid)}
												onChange={(e) =>
													change(
														"gpu_uuids",
														e.target.checked
															? [...form.gpu_uuids, g.uuid]
															: form.gpu_uuids.filter((u) => u !== g.uuid),
													)
												}
											/>
											GPU {g.index}
											<small>
												{g.processes?.length
													? t("使用中", "In use")
													: t("空闲", "Free")}
											</small>
										</label>
									))}
								</fieldset>
							</>
						) : (
							<>
								<Field label={t("服务地址", "Service URL")}>
									<Input
										value={form.base_url}
										onChange={(e) => change("base_url", e.target.value)}
									/>
								</Field>
								<Field label={t("服务端模型 ID", "Server model ID")}>
									<Input
										value={form.model}
										onChange={(e) => change("model", e.target.value)}
									/>
								</Field>
								<Field
									label={t(
										"API 密钥（仅保存在服务器）",
										"API key (stored on server only)",
									)}
								>
									<Input
										type="password"
										autoComplete="new-password"
										value={key || ""}
										placeholder={
											editId
												? t(
														"留空保留原密钥",
														"Leave blank to keep existing key",
													)
												: ""
										}
										onChange={(e) => setKey(e.target.value || undefined)}
									/>
								</Field>
								{form.kind === "image-api" && (
									<label>
										<input
											type="checkbox"
											checked={form.image_edit}
											onChange={(e) => change("image_edit", e.target.checked)}
										/>
										{t(
											"服务支持 images/edits 图片编辑",
											"Service supports images/edits",
										)}
									</label>
								)}
							</>
						)}
					</div>
					{local && (
						<>
							<Button
								variant="secondary"
								onClick={() =>
									setForm((p) => ({
										...p,
										model: installed(p.kind === "image-local" ? p.checkpoint : "h3-shared") || p.model,
										transformer_path:
											installed(`h3-${p.partition}-int8`) || p.transformer_path,
										lora_path:
											installed(`h3-${p.partition}-turbo4`) || p.lora_path,
									}))
								}
							>
								{t("填入已下载组件", "Use downloaded components")}
							</Button>
							<p className="oc-muted">
								{t(
									"H3 仍在开发验证中，1.5.0 轮子暂不包含此接口。可在安装引导导入开发环境；不会自动升级或替换聊天环境。",
									"H3 is under development and is not included in the 1.5.0 wheel. Import a compatible development runtime in Setup; chat environments are preserved.",
								)}
							</p>
						</>
					)}
					{form.kind === "image-api" && (
						<p className="oc-muted">
							{t(
								"要求 /v1/models 和 OpenAI 兼容图片接口，返回 b64_json。图片能力取决于接入的服务；不是所有聊天模型都能生图。",
								"Requires /v1/models and OpenAI-compatible image endpoints returning b64_json. Image capabilities depend on the configured service.",
							)}
						</p>
					)}
					<div className="oc-actions">
						<Action
							run={async () => {
								const {
									id: _id,
									workflows: _w,
									instance: _i,
									has_key: _k,
									...fields
								} = form as typeof form & Partial<Service>;
								await mutation("/api/creative/services", {
									...fields,
									...(editId ? { id: editId } : {}),
									...(key !== undefined ? { api_key: key } : {}),
								});
								setEditing(false);
							}}
						>
							{t("保存服务", "Save service")}
						</Action>
						<Button variant="ghost" onClick={() => setEditing(false)}>
							{t("取消", "Cancel")}
						</Button>
					</div>
				</form>
			)}
			<details className="oc-canvas-components" open={catalog.components.some(c => !c.installed) ? true : undefined}>
				<summary>
					<Boxes size={16} />
					{t("ModelScope 模型组件", "ModelScope components")}
				</summary>
				<p className="oc-muted">
					{t(
						"公共组件只下载一次。以下为已核实来源的 H3 开发适配组件，正式质量验收尚未完成。下载后执行 SHA256 校验。",
						"Shared components download once. These source-verified components target H3 development; production quality validation is pending. Downloads are SHA256 checked.",
					)}
				</p>
				{catalog.components.map((c) => {
					const job = jobs?.items.find(
						(j) =>
							j.kind === "creative_download" &&
							(j as Job & { component_id?: string }).component_id === c.id,
					);
					const active =
						job && !["completed", "failed", "cancelled"].includes(job.state);
					return (
						<div key={c.id} className="oc-canvas-component">
							<div>
								<strong>{c.name}</strong>
								<small>
									{bytes(c.bytes)} · {c.repo_id}
								</small>
								{c.installed && <small>{t("已下载", "Downloaded")}</small>}
							</div>
							{active ? (
								<div>
									<small>
										{bytes(
											(job as Job & { downloaded_bytes?: number })
												.downloaded_bytes || 0,
										)}{" "}
										/ {bytes(c.bytes)} ·{" "}
										{bytes(
											(job as Job & { bytes_per_second?: number })
												.bytes_per_second || 0,
										)}
										/s
									</small>
									<Action
										variant="ghost"
										run={() => mutation(`/api/jobs/${job.id}/cancel`)}
									>
										{t("取消下载", "Cancel download")}
									</Action>
								</div>
							) : (
								<Action
									variant="outline"
									run={() =>
										mutation(`/api/creative/components/${c.id}/download`)
									}
									disabled={!!c.installed}
								>
									<Download size={14} />
									{t("下载", "Download")}
								</Action>
							)}
							{job?.state === "failed" && <ErrorNotice error={job.error} />}
							<a href={c.evidence} target="_blank" rel="noreferrer">
								{t("适配记录", "Evidence")} ↗
							</a>
						</div>
					);
				})}
			</details>

		</Modal>
	);
}
