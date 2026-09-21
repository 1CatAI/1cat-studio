// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import {
  useState,
  useEffect,
  useId,
  Children,
  isValidElement,
  cloneElement,
  useLayoutEffect,
  useRef,
  type ReactNode,
  type RefObject,
} from "react";
import { useLocale } from "@/onecat/locale";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { Label } from "@/onecat/ui";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/onecat/ui";
import { AlertCircle, Info, LoaderCircle, Check, Copy, X } from "lucide-react";
import { toast } from "sonner";
import { refreshData, copyText } from "./api";
import { TooltipIconButton } from "@/onecat/ui";
import { Phase, useInterfaceMotion, MOTION } from "./motion";
import { AnimatePresence, motion } from "motion/react";

export function useText() {
  const locale = useLocale();
  return (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
}
export function ErrorNotice({ error }: { error?: string }) {
  const t = useText();
  const detailed = !!error && (error.length > 220 || /Traceback|\n.*File "/.test(error));
  const { enabled } = useInterfaceMotion();
  return <AnimatePresence initial={false}>{error ? (
    <motion.div key="error" role="alert" className="oc-error" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} transition={{ duration: enabled ? MOTION.state : 0 }}>
      <AlertCircle size={16} />
      {detailed ? <div>
        <strong>{/out of memory|CUDA error: out of memory/i.test(error)
          ? t("显存不足，请检查模型和上下文配置。", "Insufficient GPU memory. Check the model and context settings.")
          : t("操作未完成，查看详情了解原因。", "The operation could not finish. View details for the cause.")}</strong>
        <details><summary>{t("查看错误详情", "Error details")}</summary><pre className="oc-log">{error}</pre></details>
      </div> : error}
    </motion.div>
  ) : null}</AnimatePresence>;
}
export function Page({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    const scroll = element?.closest(".oc-main");
    if (!element || !scroll) return;
    const key = "onecat:scroll:" + location.pathname;
    let target = 0;
    try { target = Math.max(0, Number(sessionStorage.getItem(key)) || 0); } catch { /* Storage may be disabled. */ }
    let position = target;
    let restored = target === 0;
    if (!target) scroll.scrollTop = 0;
    const restore = () => {
      if (!restored && scroll.scrollHeight - scroll.clientHeight >= target) {
        scroll.scrollTop = target;
        restored = true;
      }
    };
    const observer = new ResizeObserver(restore);
    observer.observe(element);
    restore();
    const remember = () => { if (restored) position = scroll.scrollTop; };
    scroll.addEventListener("scroll", remember, { passive: true });
    const interacted = () => { restored = true; };
    scroll.addEventListener("wheel", interacted, { passive: true });
    scroll.addEventListener("touchstart", interacted, { passive: true });
    return () => {
      observer.disconnect();
      scroll.removeEventListener("scroll", remember);
      scroll.removeEventListener("wheel", interacted);
      scroll.removeEventListener("touchstart", interacted);
      try { sessionStorage.setItem(key, String(position)); } catch { /* Scrolling works without storage. */ }
    };
  }, []);
  return (
    <section className="oc-page" ref={ref}>
      <header className="oc-page-heading">
        <div>
          <h1>{title}</h1>
          {description && <p>{description}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}
export function Empty({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div className="oc-empty">
      <h2>{title}</h2>
      {description && <p>{description}</p>}
      {children}
    </div>
  );
}
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  const id = useId();
  let bound = false;
  function bind(node: ReactNode): ReactNode {
    if (
      !isValidElement<{
        children?: ReactNode;
        value?: unknown;
        checked?: boolean;
        id?: string;
        "aria-labelledby"?: string;
      }>(node)
    )
      return node;
    if (
      !bound &&
      (node.type === Input ||
        ["input", "select", "textarea"].includes(String(node.type)) ||
        node.props.value !== undefined ||
        node.props.checked !== undefined)
    ) {
      bound = true;
      return cloneElement(node, { id, "aria-labelledby": id + "-label" });
    }
    return node.props.children
      ? cloneElement(node, {
          children: Children.map(node.props.children, bind),
        })
      : node;
  }
  return (
    <div className="oc-field">
      <div className="oc-field-label">
        <Label id={id + "-label"} htmlFor={id}>{label}</Label>
        {hint && <TooltipIconButton type="button" tooltip={hint} aria-label={label + " · ?"}><Info size={14} /></TooltipIconButton>}
      </div>
      {Children.map(children, bind)}
    </div>
  );
}
export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  // Keep the text the user is typing. Coercing every keystroke with Number()
  // turned an emptied field into 0, which then could not be deleted and pushed
  // the next digits behind it. Empty stays empty here; the value is committed,
  // clamped and turned back into a number on blur or Enter.
  const [draft, setDraft] = useState<string | null>(null);
  const commit = (raw: string) => {
    setDraft(null);
    const trimmed = raw.trim();
    const parsed = Number(trimmed);
    if (trimmed === "" || !Number.isFinite(parsed)) {
      return;
    }
    let next = Math.trunc(parsed);
    if (min !== undefined) {
      next = Math.max(min, next);
    }
    if (max !== undefined) {
      next = Math.min(max, next);
    }
    if (next !== value) {
      onChange(next);
    }
  };
  return (
    <Field label={label}>
      <Input
        type="number"
        inputMode="numeric"
        value={draft ?? String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          setDraft(raw);
          const parsed = Number(raw);
          if (
            raw.trim() !== "" &&
            Number.isFinite(parsed) &&
            (min === undefined || parsed >= min) &&
            (max === undefined || parsed <= max)
          ) {
            onChange(Math.trunc(parsed));
          }
        }}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            commit((e.target as HTMLInputElement).value);
          }
        }}
        min={min}
        max={max}
        step={step}
      />
    </Field>
  );
}
export function Action({
  children,
  run,
  success,
  variant = "default",
  disabled = false,
}: {
  children: ReactNode;
  run: () => Promise<unknown>;
  success?: string;
  variant?: "default" | "outline" | "ghost" | "destructive";
  disabled?: boolean;
}) {
  const [state, setState] = useState("idle");
  const inFlight = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const nodes = Children.toArray(children);
  const icon = isValidElement(nodes[0]) ? nodes.shift() : null;
  return (
    <Button variant={variant} aria-busy={state === "busy" || undefined} disabled={state === "busy" || disabled}
      onClick={async () => {
        if (inFlight.current) return;
        inFlight.current = true;
        clearTimeout(timer.current);
        setState("busy");
        try {
          await run();
          refreshData();
          setState("success");
          if (success) toast.success(success);
        } catch (e) {
          setState("failed");
          toast.error((e as Error).message);
        } finally {
          inFlight.current = false;
          timer.current = setTimeout(() => setState("idle"), 1400);
        }
      }}>
      <Phase phase={state} className="oc-action-icon">
        {state === "busy" ? <LoaderCircle className="animate-spin" /> : state === "success" ? <Check /> : state === "failed" ? <X /> : icon}
      </Phase>
      {nodes}
    </Button>
  );
}

export function CopyButton({ value }: { value: string }) {
  const t = useText();
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  return <TooltipIconButton tooltip={copied ? t("已复制", "Copied") : t("复制", "Copy")} aria-label={t("复制", "Copy")}
    onClick={async () => {
      try {
        await copyText(value); setCopied(true); clearTimeout(timer.current);
        timer.current = setTimeout(() => setCopied(false), 1400);
      } catch (error) { toast.error((error as Error).message); }
    }}><Phase phase={String(copied)} className="oc-action-icon">{copied ? <Check /> : <Copy />}</Phase></TooltipIconButton>;
}

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  returnFocusRef,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  returnFocusRef?: RefObject<HTMLElement | null>;
}) {
  const origin = useRef<HTMLElement | null>(null);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={"oc-dialog" + (footer ? " oc-dialog-fixed" : "")} onOpenAutoFocus={() => { origin.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; }} onCloseAutoFocus={(event) => {
        const target = returnFocusRef?.current || origin.current;
        if (target?.isConnected && !target.matches(":disabled")) {
          event.preventDefault();
          target.focus({ preventScroll: true });
        }
      }}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className={description ? undefined : "sr-only"}>{description || title}</DialogDescription>
        </DialogHeader>
        {footer ? <><div className="oc-dialog-body">{children}</div><div className="oc-dialog-footer">{footer}</div></> : children}
      </DialogContent>
    </Dialog>
  );
}
export function format(value: number | null | undefined, digits = 1) {
  return value == null
    ? "—"
    : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}
export function bytes(value: number) {
  return value >= 1024 ** 3
    ? format(value / 1024 ** 3, 1) + " GB"
    : value >= 1024 ** 2 ? format(value / 1024 ** 2, 1) + " MB"
    : value >= 1024 ? format(value / 1024, 1) + " KB"
    : format(value, 0) + " B";
}
export function saveFile(name: string, value: unknown) {
  const blob = new Blob(
    [typeof value === "string" ? value : JSON.stringify(value, null, 2)],
    { type: typeof value === "string" ? "text/plain" : "application/json" },
  );
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function jobLabel(stage: string, t: ReturnType<typeof useText>) {
  const labels: Record<string, [string, string]> = {
    ready: ["就绪", "Ready"],
    running: ["进行中", "Running"],
    loading: ["启动中", "Loading"],
    stopped: ["已停止", "Stopped"],
    failed: ["失败", "Failed"],
    completed: ["已完成", "Completed"],
    cancelled: ["已取消", "Cancelled"],
    invalid: ["测量无效", "Invalid measurement"],
    unavailable: ["检查服务中", "Checking service"],
    queued: ["等待中", "Queued"],
    starting: ["准备中", "Starting"],
    checking: ["检查环境", "Checking environment"],
    awaiting_system_authorization: ["等待系统管理员授权", "Awaiting system authorization"],
    checking_gpu_control: ["检查 GPU 控制权限", "Checking GPU controls"],
    downloading: ["下载中", "Downloading"],
    resolving_model: ["检查模型文件", "Resolving model"],
    checking_model: ["校验模型", "Checking model"],
    install_runtime: ["安装推理环境", "Install runtime"],
    creative_load: ["加载创作模型", "Load creative model"],
    creative_stop: ["停止创作服务", "Stop creative service"],
    creative_generate: ["画布生成任务", "Canvas generation"],
    creative_download: ["下载创作组件", "Download creative component"],
    encoding: ["编码素材", "Encoding references"],
    denoising: ["去噪中", "Denoising"],
    decoding: ["解码中", "Decoding"],
    generating: ["生成中", "Generating"],
    downloading_runtime: ["准备下载发行包", "Preparing release download"],
    checking_runtime_wheels: ["检查 wheel 依赖", "Checking wheel dependencies"],
    waiting_for_runtime: ["等待同一环境的安装任务", "Waiting for runtime installation"],
    installing_python: ["准备 Python", "Preparing Python"],
    installing_torch: ["安装 PyTorch", "Installing PyTorch"],
    installing_dependencies: ["安装运行依赖", "Installing dependencies"],
    validating: ["验证运行环境", "Validating runtime"],
    extracting: ["导入离线环境", "Importing offline runtime"],
    loading_weights: ["加载权重", "Loading weights"],
    compiling: ["编译模型", "Compiling"],
    warming_up: ["预热中", "Warming up"],
    waiting_for_requests: ["等待当前请求完成", "Draining requests"],
    waiting_for_engine: ["等待模型启动、停止或校准完成", "Waiting for model operation or calibration"],
    applying_gpu_settings: ["正在应用 GPU 设置", "Applying GPU settings"],
    recovering_gpu_settings: ["恢复上次未完成的设置", "Recovering interrupted settings"],
    stopping: ["卸载模型", "Unloading model"],
    releasing_model: ["释放原模型显存", "Releasing previous model"],
    capturing_graphs: ["捕获 CUDA Graph", "Capturing CUDA graphs"],
    checking_service: ["检查服务就绪", "Checking readiness"],
    measuring: ["正在测量", "Measuring"],
    applying_test_setting: ["应用测试档位", "Applying test setting"],
    preparing_workload: ["准备固定测试输入", "Preparing workload"],
    packing_runtime: ["打包离线环境", "Packing runtime"],
  };
  const pair = labels[stage];
  return pair ? t(...pair) : stage;
}
