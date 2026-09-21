// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
export async function readFastApiError(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text);
    const detail = body.detail ?? body.error?.message ?? body.message;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map(item => {
      const location = Array.isArray(item.loc) ? item.loc.filter((p: unknown) => p !== "body").join(".") : "";
      return [location, typeof item.msg === "string" ? item.msg : "Invalid value"].filter(Boolean).join(": ");
    }).join("; ");
  } catch { /* Proxies may return a plain-text error instead of JSON. */ }
  return text.trim().slice(0, 1000) || `HTTP ${response.status} ${response.statusText}`;
}
