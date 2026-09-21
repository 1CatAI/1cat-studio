// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
// Coalesce UI paints only. The stream parser, stored text and token metrics stay lossless.
export function streamPaint(paint: () => void, interval = 50) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let frame: number | undefined;
  let dirty = false;
  function flush() {
    clearTimeout(timer);
    if (frame !== undefined) cancelAnimationFrame(frame);
    timer = undefined;
    frame = undefined;
    if (dirty) {
      dirty = false;
      paint();
    }
  }
  return {
    schedule() {
      dirty = true;
      if (timer !== undefined || frame !== undefined) return;
      timer = setTimeout(() => {
        timer = undefined;
        frame = requestAnimationFrame(flush);
      }, interval);
    },
    flush,
  };
}
