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
  if (showWhen === "ready" && !data?.available) return null;
  // The setup page owns the guided flow until GPU control actually works, so
  // the wizard is never hidden behind a collapsed section when it is needed.
  if (showWhen === "incomplete" && (!data || data.available)) return null;

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
          <span className={data?.available ? "oc-status-good" : "oc-muted"}>
            {data?.available
              ? t("已启用", "Enabled")
              : busy
                ? t("等待系统授权", "Awaiting authorization")
                : t("待绑定", "Not bound")}
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
      <ErrorNotice error={error || data?.error || undefined} />
    </section>
  );

  function renderBody() {
    if (!data) return null;
    if (data.stage === "unsupported")
      return (
        <p className="oc-muted">
          {t(
            "安装包缺少 GPU 控制组件，请更新 Studio 后再试。",
            "This build is missing the GPU control component; update Studio and retry.",
          )}
        </p>
      );
    if (data.stage === "no_gpus")
      return (
        <p className="oc-muted">
          {t(
            "尚未检测到可用的 NVIDIA GPU。接入显卡后此处会自动出现绑定向导。",
            "No NVIDIA GPU detected. The binding wizard appears here once a GPU is present.",
          )}
        </p>
      );
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
          <p className="oc-muted">
            {t(
              "可在性能与能效页切换功耗模式；更换显卡后 Studio 会自动按同一范围重新绑定。",
              "Change power modes on Performance. Replacing the cards re-binds the same scope automatically.",
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
              "此服务器没有可用的授权方式（sudo 无密码或桌面授权窗口）。请管理员在安装 Studio 时完成 GPU 控制授权。",
              "No authorization path is available here (passwordless sudo or a desktop prompt). An administrator must authorize GPU control while installing Studio.",
            )}
          </p>
        </div>
      );

    // unbound / changed / upgrade: walk the operator through binding.
    return (
      <div className="oc-spaced">
        {data.hardware_changed && (
          <p className="oc-status-warn oc-row" style={{ justifyContent: "flex-start", gap: 7 }}>
            <AlertTriangle size={15} />
            {t(
              `原绑定的 ${data.missing_gpu_count} 张显卡已不在本机，请重新绑定。`,
              `${data.missing_gpu_count} previously bound GPUs are no longer present; re-bind to continue.`,
            )}
          </p>
        )}
        {data.upgrade_required && (
          <p className="oc-muted">
            {t(
              "新版控制组件已随 Studio 提供，授权后即可更新；现有权限与功耗设置会保留。",
              "Studio ships an updated component. Authorize to upgrade; existing permissions and power settings are kept.",
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
                  "Pin exact serials; replacing a card needs a re-bind",
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

  function authorizationHint() {
    return data?.desktop_authorization
      ? t(
          "授权窗口会显示在服务器桌面。密码由系统处理，Studio 不接收或保存。",
          "The authorization dialog opens on the server desktop. The system handles the password; Studio never receives or stores it.",
        )
      : t(
          "使用无密码 sudo 在当前用户的终端完成授权；密码不会经过 Studio。",
          "Authorization uses passwordless sudo in the current user's terminal; no password passes through Studio.",
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
