"""The desktop launcher proves local OS access before opening Studio."""
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .. import auth

ADDRESS = "127.0.0.2"
PORT = 18888
ORIGIN = f"http://{ADDRESS}:{PORT}"
LABEL = "1Cat local desktop"
router = APIRouter()


def ticket_dir():
    directory = Path.home() / ".local/state/onecat-studio/desktop-tickets"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def issue_ticket():
    directory = ticket_dir()
    for old in directory.iterdir():
        try:
            if time.time() - old.stat().st_mtime > 120:
                old.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
    value = secrets.token_hex(32)
    (directory / hashlib.sha256(value.encode()).hexdigest()).touch(mode=0o600, exist_ok=False)
    return value


def local_listener(request):
    return os.environ.get("ONECAT_LOCAL_DESKTOP") == "1" and tuple(request.scope.get("server") or ()) == (ADDRESS, PORT)


def check_origin(request):
    if (request.headers.get("host") != f"{ADDRESS}:{PORT}"
            or request.headers.get("sec-fetch-site") == "cross-site"
            or request.headers.get("origin") not in {None, ORIGIN}):
        raise HTTPException(403, "Local Studio origin required")


def local_session(request):
    if not local_listener(request):
        return False
    check_origin(request)
    credential = auth.require_admin(request)
    if credential["name"] != LABEL:
        raise HTTPException(403, "请从本机桌面打开 1Cat Studio")
    return True


def private_id(value):
    return isinstance(value, str) and value.startswith("desktop-")


def authorize_id(value, local):
    if private_id(value) and not local:
        raise HTTPException(404, "Not found")


@router.get("/desktop-activate", include_in_schema=False)
def activation_page(request: Request):
    if not local_listener(request):
        raise HTTPException(404)
    check_origin(request)
    return HTMLResponse("""<!doctype html><meta charset="utf-8"><title>1Cat Studio</title>
<body>正在打开本机 Studio…<script>
const ticket=location.hash.slice(1); history.replaceState(null,"","/desktop-activate");
fetch("/desktop-activate",{method:"POST",body:ticket,credentials:"same-origin"})
.then(r=>{if(r.ok) location.replace("/agent"); else document.body.textContent="请重新点击桌面的 1Cat Studio";});
</script>""", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                       "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"})


@router.post("/desktop-activate", include_in_schema=False)
async def activate(request: Request):
    if not local_listener(request):
        raise HTTPException(404)
    check_origin(request)
    if request.headers.get("origin") != ORIGIN:
        raise HTTPException(403)
    token = await request.body()
    if not re.fullmatch(b"[0-9a-f]{64}", token):
        raise HTTPException(403)
    path = ticket_dir() / hashlib.sha256(token).hexdigest()
    try:
        stamp = path.stat().st_mtime
        path.unlink()
    except FileNotFoundError:
        raise HTTPException(403)
    if time.time() - stamp > 120:
        raise HTTPException(403)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.set_cookie(auth.COOKIE, auth.issue(LABEL, "admin", time.time()+86400),
                        httponly=True, samesite="strict", max_age=86400)
    return response


if __name__ == "__main__":
    handler = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/http"],
                             capture_output=True, text=True)
    browser = "xdg-open" if handler.stdout.strip() else shutil.which("firefox") or "xdg-open"
    os.execlp(browser, browser, ORIGIN + "/desktop-activate#" + issue_ticket())
