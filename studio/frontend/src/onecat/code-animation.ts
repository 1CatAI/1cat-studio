// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useLayoutEffect, useRef, type RefObject } from "react";

const selector = '[data-streamdown="code-block-body"] pre > code';
const highlightName = "onecat-pending-code";
const graphemes = typeof Intl.Segmenter === "function"
  ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
  : undefined;

type Position = { row: number; offset: number };
type Stop = Position & { node: Text; nodeOffset: number };

// Shiki can split a grapheme across token spans. Map only the newly received
// suffix to DOM boundaries; preceding lines never need parsing or highlighting again.
function appendStops(row: Element, index: number, start: number, stops: Stop[]) {
  const suffix = (row.textContent || "").slice(start);
  if (!suffix || !graphemes) return;
  const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode() as Text | null;
  let base = 0;
  for (const { index: i, segment } of graphemes.segment(suffix)) {
    const offset = start + i + segment.length;
    while (node && offset > base + node.length) {
      base += node.length;
      node = walker.nextNode() as Text | null;
    }
    if (node) stops.push({ row: index, offset, node, nodeOffset: offset - base });
  }
}

export function useCodeAnimation(root: RefObject<HTMLDivElement | null>, code: boolean, enabled: boolean) {
  const cursor = useRef<Position>({ row: 0, offset: 0 });
  useLayoutEffect(() => {
    const block = root.current;
    if (!code || !block) return;
    const registry = globalThis.CSS?.highlights;
    const animate = enabled && !!graphemes && !!registry && typeof Highlight !== "undefined";
    const mask = animate ? registry.get(highlightName) || new Highlight() : null;
    if (mask && !registry.has(highlightName)) registry.set(highlightName, mask);

    const range = document.createRange();
    const queued = new Set<HTMLElement>();
    const fades = new Set<Animation>();
    let frame = 0;
    let lastArrival = 0, cadence = 96, deadline = 0;
    let lastEnd: Position | undefined;

    function reset() {
      cancelAnimationFrame(frame);
      mask?.delete(range);
      for (const row of queued) row.style.removeProperty("display");
      queued.clear();
    }

    const sync = () => {
      cancelAnimationFrame(frame);
      const body = block.querySelector(selector);
      const rows = body?.children;
      if (!rows?.length) { reset(); return; }
      const end = { row: rows.length - 1, offset: rows[rows.length - 1].textContent?.length || 0 };
      const now = performance.now();
      const grew = lastEnd?.row !== end.row || lastEnd.offset !== end.offset;
      if (grew) {
        const elapsed = now - lastArrival;
        if (lastArrival && elapsed > 8) cadence = Math.min(150, Math.max(40, cadence * .55 + elapsed * .45));
        lastArrival = now;
        deadline = now + cadence;
      }
      lastEnd = end;
      const start = cursor.current;
      // Show history, replacement snapshots, and background-tab catch-up immediately.
      const showAll = () => {
        reset();
        cursor.current = end;
      };
      if (!mask || document.hidden || now >= deadline || end.row < start.row || end.row - start.row > 48) {
        showAll();
        return;
      }
      let remaining = -start.offset;
      for (let i = start.row; i <= end.row; i++) remaining += rows[i].textContent?.length || 0;
      if (remaining <= 0 || remaining > 2048) {
        showAll();
        return;
      }

      const stops: Stop[] = [];
      for (const row of queued) {
        if (row.parentElement !== body) {
          row.style.removeProperty("display");
          queued.delete(row);
        }
      }
      for (let i = start.row; i <= end.row; i++) {
        const row = rows[i] as HTMLElement;
        if (i > start.row && !queued.has(row)) {
          row.style.display = "none";
          queued.add(row);
        }
        appendStops(row, i, i === start.row ? start.offset : 0, stops);
      }
      if (!stops.length) { showAll(); return; }

      const paint = (t: number) => {
        const progress = Math.min(1, Math.max(0, (t - now) / (deadline - now)));
        const pos = stops[Math.max(0, Math.ceil(progress * stops.length) - 1)];
        for (let i = cursor.current.row; i <= pos.row; i++) {
          const row = rows[i] as HTMLElement;
          if (queued.delete(row)) {
            row.style.removeProperty("display");
            const fade = row.animate([{ opacity: .82 }, { opacity: 1 }], { duration: 100, easing: "ease-out" });
            fade.id = "onecat-code-arrival";
            fades.add(fade);
            fade.onfinish = () => fades.delete(fade);
          }
        }
        cursor.current = { row: pos.row, offset: pos.offset };
        if (pos === stops[stops.length - 1]) { showAll(); return; }
        range.setStart(pos.node, pos.nodeOffset);
        range.setEnd(rows[pos.row], rows[pos.row].childNodes.length);
        mask.add(range);
        frame = requestAnimationFrame(paint);
      };
      // Install the range before the browser paints the incoming token spans.
      paint(now);
    };
    const observer = new MutationObserver(sync);
    observer.observe(block, { childList: true, subtree: true, characterData: true });
    document.addEventListener("visibilitychange", sync);
    sync();
    return () => {
      observer.disconnect();
      document.removeEventListener("visibilitychange", sync);
      reset();
      for (const fade of fades) fade.cancel();
    };
  }, [root, code, enabled]);
}
