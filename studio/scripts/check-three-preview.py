#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Render previews in the opaque sandbox without inference or a GPU.

HTML runs natively (importmaps and CDN modules need HTTPS access); JSX runs
through the bundled preview runtime and works offline.
"""

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def native_html(source, csp, prelude, import_map):
    """Python twin of nativeHtml() in preview-panel.tsx."""
    script = lambda value: value.replace("</script", "<\\/script")  # noqa: E731
    head = (
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        f"<script>{script(prelude)}</script>"
        + "<style>[hidden]{display:none!important}</style>"
        + (
            ""
            if re.search(r"<script\b[^>]*type\s*=\s*[\"']?importmap", source, re.I)
            else f'<script type="importmap">{import_map}</script>'
        )
    )
    match = re.search(r"<head\b[^>]*>", source, re.I) or re.search(r"<html\b[^>]*>", source, re.I)
    if match:
        tag = head if match.group(0).lower().startswith("<head") else f"<head>{head}</head>"
        return source[: match.end()] + tag + source[match.end() :]
    return f"<!doctype html><html><head>{head}</head><body>{source}</body></html>"


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime", type=Path, default=ROOT / "studio/frontend/public/preview/runtime.js"
    )
    parser.add_argument("--browser")
    parser.add_argument("--html", type=Path, help="also render this generated HTML page")
    args = parser.parse_args()
    runtime = args.runtime.read_text().replace("</script", "<\\/script")
    panel = (ROOT / "studio/frontend/src/onecat/preview-panel.tsx").read_text()
    csp = json.loads(re.search(r'export const PREVIEW_CSP\s*=\s*("[^\n]+");', panel).group(1))
    prelude = re.search(r"const HTML_PRELUDE = `(.*?)`;", panel, re.S).group(1)
    block = re.search(r"const DEFAULT_IMPORT_MAP = JSON.stringify\((.*?)\);", panel, re.S).group(1)
    pairs = re.findall(r'(?:"([^"]+)"|(\w+))\s*:\s*"(https://[^"]+)"', block)
    import_map = json.dumps({"imports": {quoted or bare: url for quoted, bare, url in pairs}})
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            executable_path=args.browser,
            args=[
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--no-sandbox",
            ],
        )
        page = browser.new_page()

        def sandbox(source, language="html", error=None, timeout=20000):
            page.set_content('<div id="host"></div>')
            page.evaluate(
                'window.previewReports = []; window.addEventListener("message", e => { if(e.data.onecat_preview) previewReports.push(e.data); });'
            )
            if language == "html":
                doc = native_html(source, csp, prelude, import_map)
            else:
                boot = "OneCatPreview.run(" + json.dumps(source) + "," + json.dumps(language) + ");"
                doc = (
                    '<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="'
                    + csp
                    + '"></head><body><div id="root"></div><script>'
                    + runtime
                    + "</script><script>"
                    + boot.replace("</script", "<\\/script")
                    + "</script></body></html>"
                )
            page.evaluate(
                'doc => { const f = document.createElement("iframe"); f.name="preview"; f.setAttribute("sandbox", "allow-scripts"); f.srcdoc=doc; document.getElementById("host").appendChild(f); }',
                doc,
            )
            page.wait_for_function(
                'previewReports.some(r => r.type === "ready" || r.type === "error")', timeout=timeout
            )
            reports = page.evaluate("previewReports")
            errors = [r["message"] for r in reports if r["type"] == "error"]
            if error:
                assert any(error in e for e in errors), errors
            else:
                assert not errors, errors
            return page.frame(name="preview")

        scene = """
const renderer = new THREE.WebGLRenderer({ antialias: false });
renderer.setSize(64, 64);
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(50, 1, .1, 100);
camera.position.z = 3;
scene.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial({color: 0xff0000})));
const controls = new OrbitControls(camera, renderer.domElement);
controls.update();
renderer.render(scene, camera);
const gl = renderer.getContext();
const pixel = new Uint8Array(4);
gl.readPixels(32,32,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);
window.renderProbe = { pixel: Array.from(pixel), meshes: scene.children.length, controls: controls.enabled };
"""
        imports = """import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';\n"""
        # HTML is the page itself: the default importmap resolves three from the CDN.
        frame = sandbox('<script type="module">' + imports + scene + "</script>")
        frame.wait_for_function("window.renderProbe !== undefined", timeout=20000)
        probe = frame.evaluate("renderProbe")
        assert probe["pixel"][0] > 200 and probe["pixel"][1] < 30, probe
        assert probe["meshes"] == 1 and probe["controls"], probe
        results.append({"case": "html-native-module-cdn", "result": "pass", "pixel": probe["pixel"]})

        sandbox("<script>throw new Error('preview boom')</script>", error="preview boom")
        results.append({"case": "html-error-report", "result": "pass"})

        # The frame may use HTTPS (CDN modules, fonts) but stays an opaque origin.
        frame = sandbox("""<script>
try { parent.document.body; window.parentBlocked=false; } catch { window.parentBlocked=true; }
try { localStorage.setItem('score', '7'); window.storage = localStorage.getItem('score'); } catch { window.storage = 'failed'; }
fetch('http://example.com/plain').then(() => window.httpBlocked=false).catch(() => window.httpBlocked=true);
</script>""")
        frame.wait_for_function("window.httpBlocked === true")
        assert frame.evaluate("parentBlocked") and frame.evaluate("storage") == "7"
        results.append({"case": "sandbox-parent-storage-plain-http", "result": "pass"})

        # JSX still runs through the bundled runtime, including common addons, offline.
        frame = sandbox(
            """import React from 'react'; import { Vector3 } from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { UnrealBloomPass } from 'https://unpkg.com/three@0.160.0/examples/jsm/postprocessing/UnrealBloomPass.js';
export default function App() { return <div id="result">{new Vector3(3,4,0).length()}{typeof EffectComposer}{typeof UnrealBloomPass}</div>; }""",
            "jsx",
        )
        frame.wait_for_selector("#result")
        assert frame.locator("#result").inner_text() == "5functionfunction"
        results.append({"case": "jsx-bundled-addons", "result": "pass"})
        sandbox(
            "import unknown from 'unknown-package'; export default () => <div>{String(unknown)}</div>;",
            "jsx",
            error="Unsupported dependency",
        )
        results.append({"case": "jsx-unsupported-dependency", "result": "pass"})
        if args.html:
            frame = sandbox(args.html.read_text(), timeout=40000)
            frame.wait_for_timeout(3000)
            canvases = frame.evaluate("document.querySelectorAll('canvas').length")
            covered = frame.evaluate(
                "[...document.querySelectorAll('[hidden]')].some(e => getComputedStyle(e).display !== 'none')"
            )
            assert not covered, "a [hidden] element is still displayed"
            later = page.evaluate("previewReports.filter(r => r.type === 'error').map(r => r.message)")
            assert canvases and not later, (canvases, later)
            results.append({"case": str(args.html), "result": "pass", "canvases": canvases})
        browser.close()
    print(json.dumps({"runtime": str(args.runtime), "cases": results}, indent=2))


if __name__ == "__main__":
    run()
