// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useCallback, useState, type SetStateAction } from "react";

/** Per-browser drafts/preferences. Changing keys never writes the old value into the new key. */
export function useBrowserState<T>(key: string, fallback: T, persistent = false) {
  const read = () => {
    try {
      const raw = (persistent ? localStorage : sessionStorage).getItem(key);
      return raw == null ? fallback : JSON.parse(raw) as T;
    } catch { return fallback; }
  };
  const [state, setState] = useState(() => ({ key, value: read() }));
  const value = state.key === key ? state.value : read();
  const update = useCallback((action: SetStateAction<T>) => {
    setState(previous => {
      const current = previous.key === key ? previous.value : read();
      const next = typeof action === "function" ? (action as (value: T) => T)(current) : action;
      try { (persistent ? localStorage : sessionStorage).setItem(key, JSON.stringify(next)); } catch { /* A full/disabled store must not block editing. */ }
      return { key, value: next };
    });
  }, [key, persistent]);
  return [value, update] as const;
}
