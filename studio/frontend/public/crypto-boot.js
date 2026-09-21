// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
// LAN HTTP retains getRandomValues, but may omit randomUUID.
(() => {
  if (!globalThis.crypto?.getRandomValues || globalThis.crypto.randomUUID) return;
  globalThis.crypto.randomUUID = () => {
    const data = crypto.getRandomValues(new Uint8Array(16));
    data[6] = (data[6] & 15) | 64;
    data[8] = (data[8] & 63) | 128;
    const hex = Array.from(data, byte => byte.toString(16).padStart(2, "0")).join("");
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
  };
})();
