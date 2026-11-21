// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import { useInterfaceMotion } from "./motion";

type DialogOrigin = "model" | "button";

function animateModelDialog(panel: HTMLElement, trigger: HTMLElement | null, closing: boolean, origin: DialogOrigin) {
  const card = origin === "model" ? trigger?.closest("article") : null;
  const bounds = panel.getBoundingClientRect();
  const source = (card ?? trigger)?.getBoundingClientRect();
  const centered = "translate(-50%, -50%)";
  let offsetX = 0, offsetY = 12, scale = .96;
  if (source && source.bottom > 0 && source.top < window.innerHeight) {
    // Use the visible part of a long card, including on a scrolled phone.
    const midY = (Math.max(0, source.top) + Math.min(window.innerHeight, source.bottom)) / 2;
    const travel = origin === "button" ? 1 : .42;
    offsetX = (source.left + source.width / 2 - bounds.left - bounds.width / 2) * travel;
    offsetY = (midY - bounds.top - bounds.height / 2) * travel;
    const minimumScale = origin === "button" ? .18 : card ? .78 : .87;
    scale = Math.max(minimumScale, Math.min(.96, source.width / bounds.width));
  }
  const timing = { duration: closing ? 215 : 330,
    easing: closing ? "cubic-bezier(.4,0,.65,1)" : "cubic-bezier(.2,.78,.22,1)",
    fill: "both" as const };
  const animations: Animation[] = [];
  function play(node: Element, frames: Keyframe[]) {
    animations.push(node.animate(closing ? frames.reverse() : frames, timing));
  }

  const originMark = card?.querySelector<HTMLElement>(".oc-publisher-mark");
  const detailMark = panel.querySelector<HTMLElement>(".oc-publisher-mark");
  const from = originMark?.getBoundingClientRect();
  const to = detailMark?.getBoundingClientRect();
  let ghost: HTMLElement | undefined;
  const scrollBody = panel.querySelector(".oc-dialog-body")?.getBoundingClientRect();
  if (originMark && detailMark && from && to && scrollBody &&
      from.top >= 0 && from.bottom <= window.innerHeight &&
      to.top >= scrollBody.top && to.bottom <= scrollBody.bottom &&
      originMark.querySelector("img")?.complete && detailMark.querySelector("img")?.complete) {
    ghost = originMark.cloneNode(true) as HTMLElement;
    ghost.classList.add("oc-model-avatar-flight");
    Object.assign(ghost.style, { left: from.left + "px", top: from.top + "px",
      width: from.width + "px", height: from.height + "px" });
    document.body.append(ghost);
    originMark.style.visibility = detailMark.style.visibility = "hidden";
    play(ghost, [{ transform: "translate(0,0)" },
      { transform: `translate(${to.left - from.left}px,${to.top - from.top}px)` }]);
  }
  const overlay = panel.previousElementSibling;
  if (overlay?.matches('[data-slot="dialog-overlay"]')) play(overlay, [{ opacity: 0 }, { opacity: 1 }]);
  play(panel, [
    { transform: `${centered} translate(${offsetX}px,${offsetY}px) scale(${scale})`, opacity: 0 },
    { transform: centered, opacity: 1 },
  ]);
  return {
    animations,
    dispose() {
      animations.forEach(animation => animation.cancel());
      if (ghost && originMark && detailMark) {
        ghost.remove();
        originMark.style.removeProperty("visibility");
        detailMark.style.removeProperty("visibility");
      }
    },
  };
}

export function useModelDialogTransition(trigger: RefObject<HTMLElement | null>, onClose: () => void, origin: DialogOrigin = "model") {
  const { enabled } = useInterfaceMotion();
  const [detail, setDetail] = useState<HTMLDivElement | null>(null);
  const previousDetail = useRef<HTMLDivElement | null>(null);
  const flight = useRef<ReturnType<typeof animateModelDialog> | null>(null);
  const closing = useRef(false);
  const finishClose = useRef(onClose);
  useLayoutEffect(() => { finishClose.current = onClose; }, [onClose]);

  function watch(current: ReturnType<typeof animateModelDialog>) {
    // The panel is the last and longest-lived participant. Cancellations also
    // occur on route changes and must never close a newly opened model.
    current.animations.at(-1)!.onfinish = () => {
      if (flight.current !== current) return;
      if (closing.current) finishClose.current();
      else { current.dispose(); flight.current = null; }
    };
  }
  useLayoutEffect(() => {
    const replacedForm = previousDetail.current && previousDetail.current !== detail;
    previousDetail.current = detail;
    if (!detail) {
      closing.current = false;
      return;
    }
    const panel = detail.closest<HTMLElement>('[role="dialog"]');
    if (!panel || !enabled || !panel.animate) {
      if (closing.current) finishClose.current();
      return;
    }
    // Selecting a saved profile can replace the form inside the same editing session.
    if (origin === "button" && replacedForm && !closing.current) return;
    const current = animateModelDialog(panel, trigger.current, false, origin);
    flight.current = current;
    watch(current);
    return () => { flight.current?.dispose(); flight.current = null; };
  }, [detail, enabled, trigger, origin]);

  function dismiss() {
    if (closing.current) return;
    closing.current = true;
    const panel = detail?.closest<HTMLElement>('[role="dialog"]');
    if (!enabled || !panel?.animate) { finishClose.current(); return; }
    if (flight.current) {
      // Escape during entry reverses from the exact frame already on screen.
      flight.current.animations.forEach(animation => animation.reverse());
      return;
    }
    const current = animateModelDialog(panel, trigger.current, true, origin);
    flight.current = current;
    watch(current);
  }
  return { setDetail, dismiss };
}
