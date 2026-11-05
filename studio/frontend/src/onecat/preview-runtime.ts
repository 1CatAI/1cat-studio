// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
// This entire bundle runs ONLY inside the opaque sandbox, never in Studio.
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";
import * as JSXRuntime from "react/jsx-runtime";
import * as Lucide from "lucide-react";
import * as Recharts from "recharts";
import * as Three from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import * as MapControls from "three/addons/controls/MapControls.js";
import * as TrackballControls from "three/addons/controls/TrackballControls.js";
import * as PointerLockControls from "three/addons/controls/PointerLockControls.js";
import * as EffectComposer from "three/addons/postprocessing/EffectComposer.js";
import * as RenderPass from "three/addons/postprocessing/RenderPass.js";
import * as UnrealBloomPass from "three/addons/postprocessing/UnrealBloomPass.js";
import * as OutputPass from "three/addons/postprocessing/OutputPass.js";
import * as ShaderPass from "three/addons/postprocessing/ShaderPass.js";
import * as Pass from "three/addons/postprocessing/Pass.js";
import * as BokehPass from "three/addons/postprocessing/BokehPass.js";
import * as FilmPass from "three/addons/postprocessing/FilmPass.js";
import * as GlitchPass from "three/addons/postprocessing/GlitchPass.js";
import * as SMAAPass from "three/addons/postprocessing/SMAAPass.js";
import * as AfterimagePass from "three/addons/postprocessing/AfterimagePass.js";
import * as OutlinePass from "three/addons/postprocessing/OutlinePass.js";
import * as FXAAShader from "three/addons/shaders/FXAAShader.js";
import * as CopyShader from "three/addons/shaders/CopyShader.js";
import * as RGBShiftShader from "three/addons/shaders/RGBShiftShader.js";
import * as VignetteShader from "three/addons/shaders/VignetteShader.js";
import * as GammaCorrectionShader from "three/addons/shaders/GammaCorrectionShader.js";
import * as RoomEnvironment from "three/addons/environments/RoomEnvironment.js";
import * as RoundedBoxGeometry from "three/addons/geometries/RoundedBoxGeometry.js";
import * as TextGeometry from "three/addons/geometries/TextGeometry.js";
import * as FontLoader from "three/addons/loaders/FontLoader.js";
import * as GLTFLoader from "three/addons/loaders/GLTFLoader.js";
import * as BufferGeometryUtils from "three/addons/utils/BufferGeometryUtils.js";
import * as SimplexNoise from "three/addons/math/SimplexNoise.js";
import * as ImprovedNoise from "three/addons/math/ImprovedNoise.js";
import * as Sky from "three/addons/objects/Sky.js";
import * as Reflector from "three/addons/objects/Reflector.js";
import * as CSS2DRenderer from "three/addons/renderers/CSS2DRenderer.js";
import * as CSS3DRenderer from "three/addons/renderers/CSS3DRenderer.js";
import * as RectAreaLightUniformsLib from "three/addons/lights/RectAreaLightUniformsLib.js";
import * as RectAreaLightHelper from "three/addons/helpers/RectAreaLightHelper.js";
import * as Stats from "three/addons/libs/stats.module.js";
import * as LilGui from "three/addons/libs/lil-gui.module.min.js";
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
  three: Three,
  "three/addons/controls/OrbitControls.js": { OrbitControls },
};
// Generated three.js pages import these addons through an importmap that points
// at a CDN; the sandbox has no network, so the common ones are bundled instead.
const threeAddons: Record<string, unknown> = {
  "controls/MapControls.js": MapControls,
  "controls/TrackballControls.js": TrackballControls,
  "controls/PointerLockControls.js": PointerLockControls,
  "postprocessing/EffectComposer.js": EffectComposer,
  "postprocessing/RenderPass.js": RenderPass,
  "postprocessing/UnrealBloomPass.js": UnrealBloomPass,
  "postprocessing/OutputPass.js": OutputPass,
  "postprocessing/ShaderPass.js": ShaderPass,
  "postprocessing/Pass.js": Pass,
  "postprocessing/BokehPass.js": BokehPass,
  "postprocessing/FilmPass.js": FilmPass,
  "postprocessing/GlitchPass.js": GlitchPass,
  "postprocessing/SMAAPass.js": SMAAPass,
  "postprocessing/AfterimagePass.js": AfterimagePass,
  "postprocessing/OutlinePass.js": OutlinePass,
  "shaders/FXAAShader.js": FXAAShader,
  "shaders/CopyShader.js": CopyShader,
  "shaders/RGBShiftShader.js": RGBShiftShader,
  "shaders/VignetteShader.js": VignetteShader,
  "shaders/GammaCorrectionShader.js": GammaCorrectionShader,
  "environments/RoomEnvironment.js": RoomEnvironment,
  "geometries/RoundedBoxGeometry.js": RoundedBoxGeometry,
  "geometries/TextGeometry.js": TextGeometry,
  "loaders/FontLoader.js": FontLoader,
  "loaders/GLTFLoader.js": GLTFLoader,
  "utils/BufferGeometryUtils.js": BufferGeometryUtils,
  "math/SimplexNoise.js": SimplexNoise,
  "math/ImprovedNoise.js": ImprovedNoise,
  "objects/Sky.js": Sky,
  "objects/Reflector.js": Reflector,
  "renderers/CSS2DRenderer.js": CSS2DRenderer,
  "renderers/CSS3DRenderer.js": CSS3DRenderer,
  "lights/RectAreaLightUniformsLib.js": RectAreaLightUniformsLib,
  "helpers/RectAreaLightHelper.js": RectAreaLightHelper,
  "libs/stats.module.js": Stats,
  "libs/lil-gui.module.min.js": LilGui,
};
for (const [path, module] of Object.entries(threeAddons))
  modules["three/addons/" + path] = module;
// Map CDN and examples/jsm spellings onto the bundled ids, e.g.
// https://unpkg.com/three@0.160.0/examples/jsm/postprocessing/RenderPass.js.
function moduleId(id: string) {
  const addon = id.match(/(?:^three\/addons\/|(?:^|\/)examples\/jsm\/)(.+)$/);
  if (addon) return "three/addons/" + addon[1];
  if (/^https?:\/\/.*\/three(?:@[^/]*)?(?:\/build\/three\.module(?:\.min)?\.js)?$/.test(id))
    return "three";
  return id;
}
// Classic HTML scripts use the bundled library without fetching a CDN.
Object.assign(window, { THREE: Three, OrbitControls });
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
    const key = moduleId(id);
    if (!Object.hasOwn(modules, key))
      throw new Error(
        `Unsupported dependency: ${id}. Built in: React, ReactDOM, Lucide, Recharts, Three.js and common three/addons (controls, postprocessing, environments, loaders, utils).`,
      );
    return modules[key];
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
              "External scripts are disabled. Use the built-in preview dependencies.",
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
