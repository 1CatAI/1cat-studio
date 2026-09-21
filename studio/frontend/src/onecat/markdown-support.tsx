// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Component, type ReactNode } from "react";
import type { ThemeInput } from "@streamdown/code";

// Official Shiki themes retain the existing syntax palette without a fork.
export const studioLightTheme: ThemeInput = "one-light";
export const studioDarkTheme: ThemeInput = "one-dark-pro";

export { safeMarkdownUrl } from "./markdown-url";

/** A malformed streamed block must not remove the rest of the conversation. */
export class MarkdownBlockBoundary extends Component<{ content: string; children: ReactNode }, { failed: boolean; source: string }> {
  state = { failed: false, source: this.props.content };
  static getDerivedStateFromError() { return { failed: true }; }
  static getDerivedStateFromProps(props: { content: string }, state: { source: string }) {
    return props.content !== state.source ? { failed: false, source: props.content } : null;
  }
  render() {
    return this.state.failed ? <pre className="oc-markdown-fallback">{this.props.content}</pre> : this.props.children;
  }
}
