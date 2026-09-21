// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/base.css";
import "./style.css";
import { initializeLocale } from "@/onecat/locale";
import { InterfaceMotion } from "./motion";
import { App } from "./app";
import { installClipboardFallback } from "./api";
installClipboardFallback();
const target = document.getElementById("root");
if (!target) throw new Error("Missing app root");
const root = createRoot(target);
Promise.resolve(initializeLocale()).then(() =>
  root.render(
    <StrictMode>
      <InterfaceMotion><App /></InterfaceMotion>
    </StrictMode>,
  ),
);
