// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { ErrorNotice, useText } from "../common";

export function CreationError({ error }: { error?: string }) {
  const t = useText();
  if (!error) return null;
  const lines = error.split("\n").filter((line) => line.trim());
  const reason =
    [...lines]
      .reverse()
      .find((line) =>
        /^(?:RuntimeError|ValueError|.*OutOfMemoryError|Error):/.test(line),
      ) ||
    lines[0] ||
    error;
  const summary = reason.length > 180 ? reason.slice(0, 180) + "…" : reason;
  return (
    <div className="oc-creation-error">
      <ErrorNotice error={summary} />
      {error !== summary && (
        <details>
          <summary>{t("查看详细原因", "Error details")}</summary>
          <pre>{error}</pre>
        </details>
      )}
    </div>
  );
}
