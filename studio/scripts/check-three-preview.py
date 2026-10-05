#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Render Three.js in the opaque preview sandbox without inference or a GPU."""

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime", type=Path, default=ROOT / "studio/frontend/public/preview/runtime.js"
    )
    parser.add_argument("--browser")
    args = parser.parse_args()
    runtime = args.runtime.read_text().replace("</script", "<\\/script")
    panel = (ROOT / "studio/frontend/src/onecat/preview-panel.tsx").read_text()
    csp = json.loads(re.search(r'export const PREVIEW_CSP\s*=\s*("[^\n]+");', panel).group(1))
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

        def sandbox(source, language="html", error=None):
            page.set_content('<div id="host"></div>')
            page.evaluate(
                'window.previewReports = []; window.addEventListener("message", e => { if(e.data.onecat_preview) previewReports.push(e.data); });'
            )
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
                'previewReports.some(r => r.type === "ready" || r.type === "error")', timeout=20000
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
document.getElementById('root').appendChild(renderer.domElement);
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
        for name, source in [
            ("module", '<script type="module">' + imports + scene + "</script>"),
            ("classic", "<script>" + scene + "</script>"),
            (
                "legacy-addon",
                '<script type="module">'
                + imports.replace("three/addons/", "three/examples/jsm/")
                + scene
                + "</script>",
            ),
        ]:
            frame = sandbox(source)
            probe = frame.evaluate("renderProbe")
            assert probe["pixel"][0] > 200 and probe["pixel"][1] < 30, probe
            assert probe["meshes"] == 1 and probe["controls"], probe
            results.append({"case": name, "result": "pass", "pixel": probe["pixel"]})

        frame = sandbox(
            """import React from 'react'; import { Vector3 } from 'three';
export default function App() { return <div id="result">{new Vector3(3,4,0).length()}</div>; }""",
            "jsx",
        )
        frame.wait_for_selector("#result")
        assert frame.locator("#result").inner_text() == "5"
        results.append({"case": "jsx-module", "result": "pass"})

        frame = sandbox("""<script>
try { parent.document.body; window.parentBlocked=false; } catch { window.parentBlocked=true; }
fetch('https://example.com/forbidden').then(() => window.fetchBlocked=false).catch(() => window.fetchBlocked=true);
</script>""")
        frame.wait_for_function("window.fetchBlocked === true")
        assert frame.evaluate("parentBlocked")
        results.append({"case": "sandbox-parent-and-network", "result": "pass"})
        sandbox(
            '<script type="module">import unknown from "unknown-package";</script>',
            error="Unsupported dependency",
        )
        results.append({"case": "unsupported-dependency", "result": "pass"})
        sandbox(
            '<script src="https://example.com/three.js"></script>',
            error="External scripts are disabled",
        )
        results.append({"case": "external-script", "result": "pass"})
        browser.close()
    print(json.dumps({"runtime": str(args.runtime), "cases": results}, indent=2))


if __name__ == "__main__":
    run()
