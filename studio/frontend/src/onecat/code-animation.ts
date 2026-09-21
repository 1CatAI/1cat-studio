// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useRef, type RefObject } from "react";

// Animate each arriving line once. Syntax highlighting can replace its token
// spans asynchronously; neither that nor appended text reanimates older lines.
// No extra character spans or React state updates are needed.
export function useCodeAnimation(
  root: RefObject<HTMLDivElement | null>,
  code: boolean,
  enabled: boolean,
) {
  const seen = useRef(0);
  useEffect(() => {
    const node = root.current;
    if (!node || !code) return;
    const active = new Set<Animation>();
    let frame: number | undefined;
    function update() {
      frame = undefined;
      const lines = node!.querySelectorAll<HTMLElement>(
        '[data-streamdown="code-block-body"] pre > code > span',
      );
      if (enabled) {
        for (let index = seen.current; index < lines.length; index++) {
          if (!lines[index].textContent?.trim()) continue;
          const animation = lines[index].animate(
            [
              { opacity: 0.25, filter: "blur(2px)" },
              { opacity: 1, filter: "blur(0px)" },
            ],
            { duration: 280, easing: "cubic-bezier(.2,.7,.2,1)" },
          );
          animation.id = "onecat-code-arrival";
          active.add(animation);
          animation.onfinish = () => {
            active.delete(animation);
            animation.cancel();
          };
        }
      }
      let visible = lines.length;
      while (visible && !lines[visible - 1].textContent?.trim()) visible--;
      seen.current = Math.max(seen.current, visible);
    }
    function schedule() {
      if (frame === undefined) frame = requestAnimationFrame(update);
    }
    const observer = new MutationObserver(schedule);
    observer.observe(node, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    schedule();
    return () => {
      observer.disconnect();
      if (frame !== undefined) cancelAnimationFrame(frame);
      for (const animation of active) animation.cancel();
    };
  }, [root, code, enabled]);
}
