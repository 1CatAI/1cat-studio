// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import { Action, ErrorNotice, useText } from "./common";
import { mutation, useQuery } from "./api";

type Board = {
  uuid: string;
  index: number;
  name: string | null;
  memory_total_mib: number | null;
  compute_capability: number[] | null;
  bound: boolean;
};

type Stage =
  | "unsupported"
  | "no_gpus"
  | "authorizing"
  | "ready"
  | "upgrade"
  | "changed"
  | "unbound"
  | "manual";

type Control = {
  bundled: boolean;
  installed: boolean;
  available: boolean;
  upgrade_required?: boolean;
  protocol: number;
  required_protocol: number;
  gpu_count: number;
  allowed_gpu_count: number;
  restricted: boolean;
  binding_mode?: string | null;
  missing_gpu_count: number;
  hardware_changed: boolean;
  can_authorize: boolean;
  desktop_authorization: boolean;
  stage: Stage;
  diagnostic?: {
    reason:
      | "visible"
      | "pci_not_visible"
      | "driver_unbound"
      | "other_driver"
      | "access_denied"
      | "driver_not_loaded"
      | "nvml_error"
      | "gpu_inaccessible"
      | "driver_error"
      | "probe_unavailable";
    smi_gpu_count: number;
    pci_gpu_count?: number | null;
    gv100_count?: number;
    pci_drivers?: string[];
  };
  options: {
    all_gpus: number;
    compute_classes: { compute_capability: number[]; count: number; name: string }[];
  };
  devices: Board[];
  job?: { id: string; state: string; stage: string } | null;
  error?: string | null;
};

type Choice = "class" | "all" | "uuids";

const BINDING_STAGES: Stage[] = ["unbound", "changed"];

function classLabel(classes: { compute_capability: number[]; count: number; name: string }[]) {
  return classes.map((c) => `${c.count} × ${c.name}`).join(" / ");
}

function memory(mib: number | null) {
  return mib == null ? "—" : (mib / 1024).toFixed(0) + " GB";
}

export function GPUControls({
  compact = false,
  showWhen = "always",
}: {
  compact?: boolean;
  showWhen?: "always" | "ready" | "incomplete";
}) {
  const t = useText();
  const { data, error } = useQuery<Control>("/api/gpu/control", 2000);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const busy = !!data?.job;
  const stage = data?.stage;
  const choosing = data ? BINDING_STAGES.includes(data.stage) : false;
  const mode: Choice = choice ?? (data?.binding_mode === "uuids" ? "class" : (data?.binding_mode as Choice)) ?? "class";
  const devices = data?.devices ?? [];
  if (showWhen === "ready" && stage !== "ready") return null;
  // The setup page owns the guided flow until GPU control actually works, so
  // the wizard is never hidden behind a collapsed section when it is needed.
  if (showWhen === "incomplete" && ((!data && !error) || stage === "ready")) return null;

  const statusLabels: Record<Stage, string> = {
    unsupported: t("缺少控制组件", "Control component missing"),
    no_gpus: t("GPU 不可用", "GPUs unavailable"),
    authorizing: t("等待系统授权", "Awaiting authorization"),
    ready: t("已启用", "Enabled"),
    upgrade: t("待更新", "Update needed"),
    changed: t("绑定需检查", "Check GPU binding"),
    unbound: t("待绑定", "Not bound"),
    manual: t("需管理员授权", "Admin authorization needed"),
  };

  // The binding is a machine-independent class by default, so the same package
  // installs on any host and re-binds itself when the cards are replaced.
  const steps = [
    { key: "detect", label: t("检测显卡", "Detect GPUs"), done: !!data?.gpu_count },
    {
      key: "bind",
      label: t("选择绑定范围", "Choose binding"),
      done: stage === "ready",
      active: choosing,
    },
    {
      key: "authorize",
      label: t("系统授权", "System authorization"),
      done: stage === "ready",
      active: stage === "authorizing" || stage === "upgrade",
    },
    { key: "ready", label: t("完成", "Done"), done: stage === "ready" },
  ];

  return (
    <section className="oc-panel" aria-label={t("GPU 控制", "GPU controls")}>
      {!compact && (
        <div className="oc-row">
          <h2>{t("GPU 控制", "GPU controls")}</h2>
          <span className={stage === "ready" ? "oc-status-good" : "oc-muted"}>
            {stage
              ? statusLabels[stage]
              : error
                ? t("检测失败", "Detection failed")
                : t("检测中…", "Checking…")}
          </span>
        </div>
      )}
      {!!data?.gpu_count && (
        <div className="oc-steps">
          {steps.map((step, index) => (
            <span key={step.key} className={step.active ? "active" : undefined}>
              {step.done ? "✓ " : `${index + 1} · `}
              {step.label}
            </span>
          ))}
        </div>
      )}
      {renderBody()}
      <ErrorNotice error={error || (stage !== "no_gpus" ? data?.error : undefined) || undefined} />
    </section>
  );

  function renderBody() {
    if (!data) return null;
    if (data.stage === "unsupported")
      return (
        <p className="oc-muted">
          {t(
            "安装包缺少 GPU 控制组件，请更新 Studio 后再试。",
            "Update Studio to get the GPU control component, then try again.",
          )}
        </p>
      );
    if (data.stage === "no_gpus") return renderDiagnostic(data.diagnostic);
    if (data.stage === "ready")
      return (
        <div className="oc-spaced">
          <span className="oc-status-good oc-row" style={{ justifyContent: "flex-start", gap: 8 }}>
            <CheckCircle2 size={16} />
            {t(
              `已绑定 ${data.allowed_gpu_count} / ${data.gpu_count} 张显卡`,
              `${data.allowed_gpu_count} of ${data.gpu_count} GPUs bound`,
            )}
          </span>
          {bindingWarning()}
          <p className="oc-muted">
            {t(
              "在「性能与能效」切换功耗模式。更换显卡后，Studio 会按原范围重新绑定。",
              "Change power modes on the Performance page. Studio rebinds the same scope when you replace GPUs.",
            )}
          </p>
          {data.restricted && (
            <p className="oc-muted">
              {t("管理员设置的显卡范围会保留。", "The administrator's GPU scope is preserved.")}
            </p>
          )}
        </div>
      );
    if (data.stage === "authorizing")
      return (
        <div className="oc-spaced">
          <span className="oc-muted oc-row" style={{ justifyContent: "flex-start", gap: 8 }}>
            <LoaderCircle className="animate-spin" size={16} />
            {t("正在等待系统授权…", "Waiting for system authorization…")}
          </span>
          <p className="oc-muted">{authorizationHint()}</p>
        </div>
      );
    if (data.stage === "manual")
      return (
        <div className="oc-spaced">
          <p className="oc-status-warn">
            {t(
              `已识别 ${data.gpu_count} 张 GPU。请让服务器管理员授权 GPU 控制。`,
              `Detected ${data.gpu_count} NVIDIA ${data.gpu_count === 1 ? "GPU" : "GPUs"}. Ask a server administrator to authorize GPU control.`,
            )}
          </p>
        </div>
      );

    // unbound / changed / upgrade: walk the operator through binding.
    return (
      <div className="oc-spaced">
        {bindingWarning()}
        {data.stage === "unbound" && (
          <p className="oc-muted">
            {data.diagnostic?.reason === "visible"
              ? t(
                  `nvidia-smi 识别到 ${data.diagnostic.smi_gpu_count} 张 GPU，Studio 尚需绑定和授权。`,
                  `nvidia-smi lists ${data.diagnostic.smi_gpu_count} GPU(s). Bind and authorize them for Studio.`,
                )
              : t(
                  `Studio 已识别 ${data.gpu_count} 张 GPU，请选择绑定范围并授权。`,
                  `Studio sees ${data.gpu_count} GPU(s). Choose a binding and authorize it.`,
                )}
          </p>
        )}
        {data.upgrade_required && (
          <p className="oc-muted">
            {t(
              "授权后安装新版 GPU 控制组件，沿用当前权限和功耗设置。",
              "Authorize the update to install the new GPU control component with your current permissions and power settings.",
            )}
          </p>
        )}
        {!data.upgrade_required && (
          <>
            <p className="oc-muted">
              {t(
                "选择 Studio 可以控制哪些显卡。推荐按架构范围绑定：换机器、换卡后无需重新配置。",
                "Choose which GPUs Studio may control. Binding by architecture is recommended: it survives changing machines and replacing cards.",
              )}
            </p>
            <div className="oc-gpu-choices">
              <Choice
                selected={mode === "class"}
                onSelect={() => setChoice("class")}
                title={t("按架构绑定（推荐）", "Bind by architecture (recommended)")}
                detail={
                  data.options.compute_classes.length
                    ? classLabel(data.options.compute_classes)
                    : t("本机全部计算卡", "Every compute GPU present")
                }
              />
              <Choice
                selected={mode === "all"}
                onSelect={() => setChoice("all")}
                title={t("绑定全部显卡", "Bind every GPU")}
                detail={t(
                  `${data.options.all_gpus} 张可控显卡`,
                  `${data.options.all_gpus} controllable GPUs`,
                )}
              />
              <Choice
                selected={mode === "uuids"}
                onSelect={() => {
                  setChoice("uuids");
                  if (!picked.length) setPicked(devices.map((d) => d.uuid));
                }}
                title={t("手动选择显卡", "Choose GPUs manually")}
                detail={t(
                  "指定具体序列号，换卡后需要重新绑定",
                  "Bind by serial number. Rebind after replacing a card.",
                )}
              />
            </div>
            {mode === "uuids" && (
              <div className="oc-gpu-choices">
                {devices.map((device) => (
                  <label className="oc-gpu-choice" key={device.uuid}>
                    <input
                      type="checkbox"
                      checked={picked.includes(device.uuid)}
                      onChange={(event) =>
                        setPicked((current) =>
                          event.target.checked
                            ? [...current, device.uuid]
                            : current.filter((value) => value !== device.uuid),
                        )
                      }
                    />
                    <span>
                      {t(`显卡 ${device.index}`, `GPU ${device.index}`)} · {device.name ?? "—"}
                      <small className="oc-muted">
                        {memory(device.memory_total_mib)}
                        {device.bound ? t(" · 已绑定", " · bound") : ""}
                      </small>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </>
        )}
        <div className="oc-spaced">
          <Action
            disabled={busy || !data.bundled || !data.can_authorize || (mode === "uuids" && !picked.length)}
            run={() =>
              mutation("/api/gpu/control/setup", {
                mode,
                uuids: mode === "uuids" ? picked : [],
                rebind: data.hardware_changed,
              })
            }
          >
            {data.upgrade_required ? <RefreshCw /> : <ShieldCheck />}
            {data.upgrade_required
              ? t("更新 GPU 控制", "Update GPU controls")
              : t("授权并启用", "Authorize and enable")}
          </Action>
          <p className="oc-muted">{authorizationHint()}</p>
        </div>
      </div>
    );
  }

  function bindingWarning() {
    const missing = data?.missing_gpu_count;
    if (!missing) return null;
    return (
      <p className="oc-status-warn oc-row" style={{ justifyContent: "flex-start", gap: 7 }}>
        <AlertTriangle size={15} />
        {t(
          `Studio 当前无法读取原绑定的 ${missing} 张 GPU。换卡后可重新绑定。若没有换卡，请先检查驱动和 PCIe 连接。`,
          `Studio cannot read ${missing} previously bound GPU(s). Rebind after a card replacement. Otherwise, check the driver and PCIe connection first.`,
        )}
      </p>
    );
  }

  function renderDiagnostic(diagnostic: Control["diagnostic"]) {
    const pciCount = diagnostic?.pci_gpu_count ?? 0;
    const gv100Count = diagnostic?.gv100_count ?? 0;
    const drivers = diagnostic?.pci_drivers?.join(" / ") ?? "";
    const messages = {
      visible: t(
        `nvidia-smi 已识别到 ${diagnostic?.smi_gpu_count} 张 GPU。Studio 无法读取设备，请检查服务的 NVML 库和设备权限。`,
        `nvidia-smi detected ${diagnostic?.smi_gpu_count} GPUs. Studio cannot read them. Check the service's NVML library and device permissions.`,
      ),
      pci_not_visible: t(
        "PCIe 未识别到 NVIDIA GPU。请检查供电、插槽连接或 GPU 直通。",
        "PCIe did not detect an NVIDIA GPU. Check card power, slot connections or GPU passthrough.",
      ),
      driver_unbound: t(
        "这些 GPU 尚未绑定内核驱动。请检查 NVIDIA 驱动模块是否加载，以及设备是否初始化成功。",
        "These GPUs have no kernel driver bound. Check whether the NVIDIA driver is loaded and the devices initialized.",
      ),
      other_driver: t(
        `检测到由 ${drivers} 驱动接管的 GPU。请核对 GPU 直通配置和 NVIDIA 驱动绑定。`,
        `PCIe shows GPUs bound to ${drivers}. Check passthrough and the NVIDIA driver binding.`,
      ),
      access_denied: t(
        "nvidia-smi 报告访问受限。请检查 Studio 服务账号的设备权限或容器中的 GPU 设备映射。",
        "nvidia-smi reports insufficient access. Check the service account's device permissions or container GPU mapping.",
      ),
      driver_not_loaded: t(
        "nvidia-smi 报告 NVIDIA 驱动未加载。请检查驱动模块和初始化日志。",
        "nvidia-smi reports that the NVIDIA driver is not loaded. Check the driver module and its initialization logs.",
      ),
      nvml_error: t(
        "nvidia-smi 无法使用 NVML。请核对 Studio 服务加载的 NVML 库与 NVIDIA 内核驱动版本。",
        "nvidia-smi cannot use NVML. Check that the service's NVML library matches the NVIDIA kernel driver.",
      ),
      gpu_inaccessible: t(
        "nvidia-smi 报告 GPU 不可访问。请检查内核日志中的 NVIDIA Xid 和 PCIe 错误。",
        "nvidia-smi reports an inaccessible GPU. Check kernel logs for NVIDIA Xid and PCIe errors.",
      ),
      driver_error: t(
        "nvidia-smi 未列出 NVIDIA GPU。请核对驱动初始化日志和设备权限。",
        "nvidia-smi did not list any NVIDIA GPUs. Check driver initialization logs and device permissions.",
      ),
      probe_unavailable: t(
        "当前无法确认 GPU 可用性。请在 Studio 服务运行的环境检查 nvidia-smi -L 和 lspci -nnk。",
        "GPU availability could not be determined. Check nvidia-smi -L and lspci -nnk in the Studio service environment.",
      ),
    };
    return (
      <div className="oc-spaced" role="status">
        {!!pciCount && (
          <p className="oc-muted">
            {gv100Count
              ? t(
                  `PCIe 已识别 ${pciCount} 张 NVIDIA GPU，其中 ${gv100Count} 张为 GV100。`,
                  `PCIe enumerated ${pciCount} NVIDIA GPU(s), including ${gv100Count} GV100 device(s).`,
                )
              : t(`PCIe 已识别 ${pciCount} 张 NVIDIA GPU。`, `PCIe enumerated ${pciCount} NVIDIA GPU(s).`)}
          </p>
        )}
        <p className="oc-status-warn">{messages[diagnostic?.reason ?? "probe_unavailable"]}</p>
      </div>
    );
  }

  function authorizationHint() {
    return data?.desktop_authorization
      ? t(
          "请在服务器桌面的系统授权窗口输入密码。",
          "Enter your password in the system authorization dialog on the server desktop.",
        )
      : t(
          "在当前用户的终端用免密 sudo 授权。",
          "Authorize from the current user's terminal with passwordless sudo.",
        );
  }
}

function Choice({
  selected,
  onSelect,
  title,
  detail,
}: {
  selected: boolean;
  onSelect: () => void;
  title: string;
  detail: string;
}) {
  return (
    <label className="oc-gpu-choice">
      <input type="radio" checked={selected} onChange={onSelect} />
      <span>
        {title}
        <small className="oc-muted">{detail}</small>
      </span>
    </label>
  );
}
