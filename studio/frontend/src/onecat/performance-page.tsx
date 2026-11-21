// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Page, useText } from "./common";
import "./styles/performance.css";
import { PowerModes } from "./power-modes";

export function PerformancePage() {
  const t = useText();
  return (
    <Page title={t("性能与能效", "Performance & efficiency")}>
      <PowerModes />
    </Page>
  );
}
