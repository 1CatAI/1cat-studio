// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Component, type ReactNode } from "react";

// A message renderer or its controls must not unmount the chat stream/composer.
export class MessageBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    console.error("[onecat message renderer]", error);
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
