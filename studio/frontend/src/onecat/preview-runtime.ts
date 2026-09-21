// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
// This entire bundle runs ONLY inside the opaque sandbox, never in Studio.
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";
import * as JSXRuntime from "react/jsx-runtime";
import * as Lucide from "lucide-react";
import * as Recharts from "recharts";
import { transform } from "sucrase";

// Generated games commonly save a score. Opaque frames cannot use origin
// storage; provide fresh frame-local stores without exposing Studio's storage.
function isolatedStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    key(index: number) {
      return [...values.keys()][index] ?? null;
    },
    getItem(key: string) {
      return values.get(String(key)) ?? null;
    },
    setItem(key: string, value: string) {
      values.set(String(key), String(value));
    },
    removeItem(key: string) {
      values.delete(String(key));
    },
    clear() {
      values.clear();
    },
  };
}
for (const name of ["localStorage", "sessionStorage"])
  Object.defineProperty(window, name, { value: isolatedStorage() });

const modules: Record<string, unknown> = {
  react: React,
  "react-dom": { ...ReactDOM, ...ReactDOMClient },
  "react-dom/client": ReactDOMClient,
  "react/jsx-runtime": JSXRuntime,
  "lucide-react": Lucide,
  recharts: Recharts,
};
const report = (type: string, message = "") =>
  parent.postMessage({ onecat_preview: true, type, message }, "*");
window.addEventListener("error", (e) => report("error", e.message));
window.addEventListener("unhandledrejection", (e) =>
  report("error", String(e.reason)),
);
// Links and forms are local interactions; no popups, navigation or form requests.
document.addEventListener(
  "click",
  (event) => {
    const link = (event.target as Element)?.closest?.("a");
    if (link && !link.getAttribute("href")?.startsWith("#"))
      event.preventDefault();
  },
  true,
);
document.addEventListener("submit", (event) => event.preventDefault(), true);

function compile(source: string, jsx = false) {
  return transform(source, {
    transforms: jsx ? ["typescript", "jsx", "imports"] : ["imports"],
    production: true,
    jsxRuntime: "classic",
  }).code;
}
function execute(code: string) {
  const module: { exports: Record<string, unknown> } = { exports: {} };
  const require = (id: string) => {
    if (!Object.hasOwn(modules, id))
      throw new Error(
        `Unsupported dependency: ${id}. Built in: React, ReactDOM, Lucide, Recharts.`,
      );
    return modules[id];
  };
  new Function("require", "module", "exports", "React", "ReactDOM", code)(
    require,
    module,
    module.exports,
    React,
    { ...ReactDOM, ...ReactDOMClient },
  );
  return module.exports;
}

class Boundary extends React.Component<
  { children: React.ReactNode },
  { error: boolean }
> {
  state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  componentDidCatch(error: Error) {
    report("error", error.message);
  }
  render() {
    return this.state.error ? null : this.props.children;
  }
}

function run(source: string, language: string) {
  try {
    if (["jsx", "tsx", "react"].includes(language)) {
      const code = compile(source, true);
      const exported = execute(code);
      const Component = exported.default || exported.App;
      if (Component)
        ReactDOMClient.createRoot(document.getElementById("root")!).render(
          React.createElement(
            Boundary,
            null,
            React.createElement(Component as React.ComponentType),
          ),
        );
      else if (
        !document.getElementById("root")?.childNodes.length &&
        !/\b(?:createRoot|hydrateRoot)\s*\(/.test(source)
      )
        throw new Error(
          "React preview needs export default, export App, or an explicit createRoot(...).render(...).",
        );
    } else {
      if (
        /<[^>]*$/.test(source) ||
        (source.match(/<script\b/gi) || []).length !==
          (source.match(/<\/script\s*>/gi) || []).length ||
        (source.match(/<style\b/gi) || []).length !==
          (source.match(/<\/style\s*>/gi) || []).length
      )
        throw new Error("Waiting for complete markup / 等待完整代码");
      const parsed = new DOMParser().parseFromString(
        source,
        language === "svg" ? "image/svg+xml" : "text/html",
      );
      if (parsed.querySelector("parsererror"))
        throw new Error("Waiting for valid SVG / 等待完整 SVG");
      const scripts = Array.from(parsed.querySelectorAll("script"));
      // Parse all executable blocks before replacing the candidate DOM.
      const code = scripts
        .filter(
          (script) =>
            !script.type ||
            [
              "module",
              "text/javascript",
              "application/javascript",
              "text/babel",
            ].includes(script.type),
        )
        .map((script) => {
          if (script.src)
            throw new Error(
              "External scripts are disabled. Use the built-in React dependencies.",
            );
          const source = script.textContent || "";
          const module = ["module", "text/babel"].includes(script.type);
          const code = module
            ? compile(source, script.type === "text/babel")
            : source;
          // Validate before replacing the candidate. Classic scripts run in
          // the frame's global scope, just as they do in a normal HTML page,
          // so inline handlers and subsequent scripts can see declarations.
          new Function(code);
          return { code, module };
        });
      parsed
        .querySelectorAll("script,meta,base,iframe,object,embed,link")
        .forEach((element) => element.remove());
      document.getElementById("root")!.innerHTML =
        language === "svg"
          ? parsed.documentElement.outerHTML
          : parsed.head.innerHTML + parsed.body.innerHTML;
      for (const block of code) {
        if (block.module) execute(block.code);
        else {
          const script = document.createElement("script");
          script.textContent = block.code;
          document.body.appendChild(script);
          script.remove();
        }
      }
    }
    requestAnimationFrame(() => requestAnimationFrame(() => report("ready")));
  } catch (error) {
    report("error", (error as Error).message);
  }
}
Object.assign(window, { OneCatPreview: { run } });
