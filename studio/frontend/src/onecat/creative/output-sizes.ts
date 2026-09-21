// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
export type Size = [number, number];
const ratios: Size[] = [[16, 9], [9, 16], [1, 1], [4, 3], [3, 4], [3, 2], [2, 3], [21, 9]];

export function aspectRatio(width: number, height: number): string {
  const closest = [...ratios].sort((a, b) =>
    Math.abs(Math.log(width / height / (a[0] / a[1]))) -
    Math.abs(Math.log(width / height / (b[0] / b[1]))),
  )[0];
  // Native dimensions are aligned to 16/32 pixels; the displayed ratio is approximate.
  if (Math.abs(width / height / (closest[0] / closest[1]) - 1) < .06) return closest.join(":");
  const gcd = (a: number, b: number): number => b ? gcd(b, a % b) : a;
  const divisor = gcd(width, height);
  return `${width / divisor}:${height / divisor}`;
}

export function sizesForRatio(sizes: Size[], ratio: string): Size[] {
  return sizes.filter(([w, h]) => aspectRatio(w, h) === ratio).sort((a, b) => a[0] * a[1] - b[0] * b[1]);
}

/** Keep the selected resolution tier when changing orientation/aspect ratio. */
export function sizeForRatio(sizes: Size[], current: Size, ratio: string): Size | undefined {
  const previous = sizesForRatio(sizes, aspectRatio(...current));
  const next = sizesForRatio(sizes, ratio);
  const tier = previous.findIndex(([w, h]) => w === current[0] && h === current[1]);
  return next[tier < 0 ? next.length - 1 : Math.min(tier, next.length - 1)];
}
