// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Component, type ReactNode } from "react";
import { Button } from "@/onecat/ui";
import { useText } from "./common";

function FailedPreview({ onClose, onRetry, error }: { onClose: () => void; onRetry: () => void; error: string }) {
  const t = useText();
  return (
    <aside className="oc-preview-panel oc-preview-failed" role="alert">
      <p>
        {t(
          "预览加载失败。请重试打开预览。",
          "The preview could not load. Open it again to retry.",
        )}
      </p>
      <div className="oc-actions">
        <Button onClick={onRetry}>{t("重试预览", "Retry preview")}</Button>
        <Button variant="outline" onClick={onClose}>{t("关闭预览", "Close preview")}</Button>
      </div>
      <details><summary>{t("错误详情", "Error details")}</summary><pre className="oc-preview-error">{error}</pre></details>
    </aside>
  );
}

export class PreviewBoundary extends Component<
  { children: ReactNode; onClose: () => void },
  { failed: boolean; error: string }
> {
  state = { failed: false, error: "" };
  static getDerivedStateFromError(error: unknown) {
    return { failed: true, error: String(error instanceof Error ? error.message : error).slice(0, 1200) };
  }
  componentDidCatch(error: unknown) {
    console.error("[preview] renderer failed", error);
  }
  render() {
    return this.state.failed ? (
      <FailedPreview onClose={this.props.onClose} onRetry={() => this.setState({ failed: false, error: "" })} error={this.state.error} />
    ) : (
      this.props.children
    );
  }
}
