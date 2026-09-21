// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
/** Markdown navigation uses web/mail links and relative application URLs only. */
export function safeMarkdownUrl(value: string): string | undefined {
  const compact = value.replace(/[\u0000-\u0020\u007f-\u009f]/g, "");
  try {
    const url = new URL(compact, "https://onecat.invalid/");
    return ["https:", "http:", "mailto:", "tel:"].includes(url.protocol) ? value : undefined;
  } catch {
    return undefined;
  }
}
