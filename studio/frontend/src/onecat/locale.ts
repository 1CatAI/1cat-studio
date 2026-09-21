// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useSyncExternalStore } from "react";

type Locale = "zh-CN" | "en";
const listeners = new Set<() => void>();
let current: Locale = "zh-CN";
function preference(): Locale {
  try {
    const saved = localStorage.getItem("onecat_locale");
    if (saved === "en" || saved === "zh-CN") return saved;
  } catch { /* Preferences are optional in restricted browser contexts. */ }
  return "zh-CN";
}
function publish(locale: Locale) {
  current = locale;
  document.documentElement.lang = locale;
  listeners.forEach(listener => listener());
}
export async function initializeLocale() {
  publish(preference());
}
export async function setLocale(locale: string) {
  const next = locale === "en" ? "en" : "zh-CN";
  try { localStorage.setItem("onecat_locale", next); } catch { /* Keep the in-memory setting. */ }
  publish(next);
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  const changed = (event: StorageEvent) => {
    if (event.key === "onecat_locale" || event.key === null) publish(preference());
  };
  window.addEventListener("storage", changed);
  return () => { listeners.delete(listener); window.removeEventListener("storage", changed); };
}
export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, () => current, () => "zh-CN");
}
