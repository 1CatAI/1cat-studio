// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useNavigate } from "@tanstack/react-router";
import { Segments } from "../motion";
import { useText } from "../common";

export function CreativeModes({ canvas }: { canvas: boolean }) {
  const t = useText(),
    navigate = useNavigate();
  return (
    <Segments
      className="oc-conversation-modes oc-creative-modes"
      role="tablist"
      aria-label={t("创作模式", "Creative mode")}
    >
      {([false, true] as const).map((value) => (
        <button
          key={String(value)}
          type="button"
          role="tab"
          aria-selected={canvas === value}
          tabIndex={canvas === value ? 0 : -1}
          onClick={() => void navigate({ to: value ? "/canvas" : "/creative" })}
          onKeyDown={(event) => {
            if (
              ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
            ) {
              event.preventDefault();
              const next =
                event.key === "Home"
                  ? false
                  : event.key === "End"
                    ? true
                    : !canvas;
              (
                event.currentTarget.parentElement?.children[
                  next ? 1 : 0
                ] as HTMLElement
              )?.focus();
              void navigate({ to: next ? "/canvas" : "/creative" });
            }
          }}
        >
          {value ? t("画布", "Canvas") : t("生成", "Generate")}
        </button>
      ))}
    </Segments>
  );
}
