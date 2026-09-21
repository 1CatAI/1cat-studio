import { Segments } from "./motion";
import { motion, useIsPresent } from "motion/react";
import { MOTION, useInterfaceMotion } from "./motion";
// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Download,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  RotateCcw,
  X,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { useText } from "./common";
import type { Artifact } from "./artifacts";
import { useBrowserState } from "./browser-state";

let runtime: Promise<string> | undefined;
function runtimeSource() {
  runtime ||= fetch("/preview/runtime.js", { credentials: "omit" })
    .then(async (r) => {
      if (!r.ok || !/javascript/i.test(r.headers.get("content-type") || ""))
        throw new Error("Preview runtime is unavailable. Retry or refresh Studio after an update.");
      return r.text();
    })
    .catch((error) => {
      runtime = undefined;
      throw error;
    });
  return runtime;
}
export const PREVIEW_CSP =
  "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; media-src data: blob:; worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
const script = (value: string) => value.replace(/<\/script/gi, "<\\/script");
export function PreviewPanel({
  artifact,
  onClose,
  streaming,
}: {
  artifact?: Artifact;
  onClose: () => void;
  streaming: boolean;
}) {
  const t = useText();
  const { enabled } = useInterfaceMotion();
  const present = useIsPresent();
  const panel = useRef<HTMLElement>(null);
  const previousRect = useRef<DOMRect | null>(null);
  const fullscreenAnimation = useRef<Animation | null>(null);
  const container = useRef<HTMLDivElement>(null),
    successful = useRef<{
      frame: HTMLIFrameElement;
      dispose: () => void;
    } | null>(null);
  const [paused, setPaused] = useState(false),
    [revision, setRevision] = useState(0),
    [full, setFull] = useState(false),
    [source, setSource] = useState(false),
    [error, setError] = useState(""),
    [codeError, setCodeError] = useState(false),
    [status, setStatus] = useState("loading");
  const [width, setWidth] = useBrowserState("onecat:preview-width", 50, true);
  useLayoutEffect(() => {
    const element = panel.current, from = previousRect.current;
    previousRect.current = null;
    if (!element || !from || !enabled) return;
    const to = element.getBoundingClientRect();
    fullscreenAnimation.current = element.animate([
      { transformOrigin: "top left", transform: `translate(${from.x - to.x}px, ${from.y - to.y}px) scale(${from.width / to.width}, ${from.height / to.height})` },
      { transformOrigin: "top left", transform: "none" },
    ], { duration: 220, easing: "cubic-bezier(.2,.7,.2,1)" });
    return () => fullscreenAnimation.current?.cancel();
  }, [full, enabled]);
  useEffect(() => {
    const origin = document.activeElement;
    const element = panel.current;
    return () => {
      if (origin instanceof HTMLElement && origin.isConnected &&
          (document.activeElement === document.body || element?.contains(document.activeElement))) origin.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => {
    if (!present) return;
    const keydown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || document.querySelector('[role="dialog"][data-state="open"]')) return;
      event.preventDefault();
      if (full) {
        previousRect.current = panel.current?.getBoundingClientRect() || null;
        fullscreenAnimation.current?.cancel();
        setFull(false);
      } else onClose();
    };
    document.addEventListener("keydown", keydown);
    return () => document.removeEventListener("keydown", keydown);
  }, [full, onClose, present]);
  const last = useRef(0);
  const current = useRef(artifact);
  current.current = artifact;
  const [candidate, setCandidate] = useState(artifact);
  useEffect(() => {
    if (paused || !present) return;
    const timer = setTimeout(
      () => {
        setCandidate(current.current);
        last.current = performance.now();
      },
      Math.max(0, 500 - (performance.now() - last.current)),
    );
    return () => clearTimeout(timer);
  }, [artifact?.source, artifact?.language, artifact?.complete, paused, present]);
  useEffect(() => {
    if (!candidate || !container.current) return;
    let active = true,
      failed = false,
      frame: HTMLIFrameElement | undefined,
      timeout: ReturnType<typeof setTimeout> | undefined;
    setStatus("loading");
    setError("");
    setCodeError(false);
    function dispose() {
      active = false;
      clearTimeout(timeout);
      window.removeEventListener("message", receive);
      if (successful.current?.frame === frame) successful.current = null;
      frame?.remove();
    }
    function receive(event: MessageEvent) {
      if (
        !active ||
        event.source !== frame?.contentWindow ||
        event.origin !== "null" ||
        !event.data?.onecat_preview
      )
        return;
      if (event.data.type === "error") {
        failed = true;
        setCodeError(true);
        setError(String(event.data.message).slice(0, 3000));
        setStatus("error");
        clearTimeout(timeout);
        // Failed candidates must stop executing, while the last good frame
        // keeps its own error/navigation listeners until it is replaced.
        if (frame !== successful.current?.frame) dispose();
      }
      if (event.data.type === "ready" && !failed) {
        if (successful.current?.frame === frame) return;
        clearTimeout(timeout);
        successful.current?.dispose();
        successful.current = { frame: frame!, dispose };
        frame!.classList.remove("oc-preview-candidate");
        setStatus("ready");
      }
    }
    window.addEventListener("message", receive);
    void runtimeSource()
      .then((bundle) => {
        if (!active || !container.current) return;
        frame = document.createElement("iframe");
        frame.title = "Code preview";
        frame.className = "oc-preview-frame oc-preview-candidate";
        frame.setAttribute("sandbox", "allow-scripts");
        frame.setAttribute("csp", PREVIEW_CSP);
        frame.setAttribute("credentialless", "");
        frame.referrerPolicy = "no-referrer";
        let loads = 0;
        frame.addEventListener("load", () => {
          if (++loads > 1 && active) {
            failed = true;
            setCodeError(false);
            setStatus("error");
            setError(
              t(
                "预览禁止跳转到其他文档，请使用上方重新运行。",
                "Preview navigation is blocked. Use Run again above.",
              ),
            );
            dispose();
          }
        });
        const invocation = `window.OneCatPreview.run(${JSON.stringify(candidate.source)},${JSON.stringify(candidate.language)});`;
        frame.srcdoc = `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${PREVIEW_CSP}"><meta name="viewport" content="width=device-width, initial-scale=1"><style>html,body{margin:0;min-height:100%;font-family:system-ui;background:#fff;color:#18181b}#root{min-height:100vh}button,input,select{font:inherit}</style></head><body><div id="root"></div><script>${script(bundle)}</script><script>${script(invocation)}</script></body></html>`;
        container.current.appendChild(frame);
        timeout = setTimeout(() => {
          if (active) {
            setCodeError(false);
            setStatus("error");
            setError(
              t(
                "预览没有完成，请检查代码后重新运行。",
                "Preview did not finish. Check the code and run again.",
              ),
            );
            dispose();
          }
        }, 10000);
      })
      .catch((e) => {
        if (active) {
          setCodeError(false);
          setStatus("error");
          setError(e.message);
          dispose();
        }
      });
    return () => {
      if (!frame || frame !== successful.current?.frame) dispose();
    };
  }, [candidate?.source, candidate?.language, revision]);
  useEffect(
    () => () => {
      successful.current?.dispose();
    },
    [],
  );
  const waiting =
    status === "error" &&
    codeError &&
    !candidate?.complete &&
    !error.startsWith("Unsupported dependency");
  function download() {
    if (!artifact) return;
    const url = URL.createObjectURL(
      new Blob([artifact.source], { type: "text/plain;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `preview.${artifact.language === "react" ? "jsx" : artifact.language}`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <motion.aside
      ref={panel}
      inert={!present || undefined}
      initial={enabled ? { opacity: 0, x: 10 } : false}
      animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 10 }}
      transition={{ duration: enabled ? MOTION.panel : 0, ease: MOTION.ease }}
      className={`oc-preview-panel ${full ? "oc-preview-full" : ""}`}
      style={{ width: `${width}%` }}
      aria-label={t("代码预览", "Code preview")}
    >
      <div
        className="oc-preview-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label={t("调整预览宽度", "Resize preview")}
        aria-valuemin={30}
        aria-valuemax={65}
        aria-valuenow={Math.round(width)}
        tabIndex={0}
        onKeyDown={(e) => {
          if (["ArrowLeft", "ArrowRight"].includes(e.key)) {
            e.preventDefault();
            setWidth((w) =>
              Math.max(30, Math.min(65, w + (e.key === "ArrowLeft" ? 2 : -2))),
            );
          }
        }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (e.currentTarget.hasPointerCapture(e.pointerId))
            setWidth(
              Math.max(
                30,
                Math.min(
                  65,
                  (((e.currentTarget.parentElement?.parentElement?.getBoundingClientRect()
                    .right || window.innerWidth) -
                    e.clientX) /
                    (e.currentTarget.parentElement?.parentElement?.getBoundingClientRect()
                      .width || window.innerWidth)) *
                    100,
                ),
              ),
            );
        }}
      />
      <div className="oc-preview-toolbar">
        <strong>{t("代码预览", "Code preview")} <small className="oc-muted">{artifact?.language?.toUpperCase()}</small></strong>
        <div className="oc-actions">
          <Segments className="oc-segments" role="group" aria-label={t("预览视图", "Preview view")}>
            <button aria-pressed={!source} onClick={() => setSource(false)}>{t("预览", "Preview")}</button>
            <button aria-pressed={source} onClick={() => setSource(true)}>{t("源码", "Source")}</button>
          </Segments>
          <Button
            size="icon"
            variant="ghost"
            title={t("重新运行", "Run again")}
            onClick={() => {
              setCandidate(current.current);
              setRevision((v) => v + 1);
            }}
          >
            <RotateCcw size={16} />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            title={
              paused
                ? t("恢复自动更新", "Resume updates")
                : t("暂停自动更新", "Pause updates")
            }
            onClick={() => setPaused(!paused)}
          >
            {paused ? <Play size={16} /> : <Pause size={16} />}
          </Button>
          <Button
            size="icon"
            variant="ghost"
            title={t("下载源码", "Download source")}
            onClick={download}
          >
            <Download size={16} />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            title={full ? t("退出全屏", "Exit fullscreen") : t("全屏", "Fullscreen")}
            onClick={() => { previousRect.current = panel.current?.getBoundingClientRect() || null; fullscreenAnimation.current?.cancel(); setFull(value => !value); }}
          >
            {full ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label={t("关闭预览", "Close preview")}
            onClick={onClose}
          >
            <X size={17} />
          </Button>
        </div>
      </div>
      <p className="oc-preview-status" role="status">
        {paused
          ? t("自动更新已暂停", "Automatic updates paused")
          : status === "loading"
            ? t("更新预览…", "Updating preview…")
            : waiting
              ? t(
                  streaming
                    ? "代码生成中，保留上次成功画面"
                    : "代码不完整，请继续生成或重新生成完整回答",
                  streaming
                    ? "Code is being generated; keeping the last successful preview"
                    : "Code is incomplete. Continue generating or regenerate the full reply.",
                )
              : status === "error"
                ? t("运行错误", "Runtime error")
                : t("运行中 · 本地沙箱", "Running · local sandbox")}
      </p>
      {error && !waiting && (
        <pre className="oc-preview-error" role="alert">
          {error}
        </pre>
      )}
      {source && <pre className="oc-preview-source">{artifact?.source}</pre>}
      <div
        className="oc-preview-viewport"
        ref={container}
        style={{ display: source ? "none" : undefined }}
      />
    </motion.aside>
  );
}
