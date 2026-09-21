#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import json
import fcntl
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def configure_bundled_gpu(python, release, probe):
    # Installing the bounded helper does not write any GPU settings. A hold on
    # hardware actions must not leave an older protocol behind after upgrading.
    helper = subprocess.run(
        [str(python), str(release / "scripts/configure-gpu-helper.py"), "--auto", "--authorize"],
        env=probe,
        check=False,
    )
    if helper.returncode:
        print("GPU controls are bundled; finish system authorization in Studio Settings.")
    elif probe.get("ONECAT_AUTO_GPU_ACTIONS") == "0":
        print("GPU component is current; automatic GPU policy recovery remains disabled.")
    else:
        subprocess.run(
            [str(python), "-c", "from onecat.gpu_control import reconcile_saved; reconcile_saved()"],
            env=probe,
            check=True,
        )


def activate_machine(python, release, probe):
    # A bundle is built on one machine and installed on another, so every preset
    # carries hardware it may never see again. Activation re-binds those presets
    # to the GPUs this host actually authorizes; presets that already match are
    # untouched, which keeps the step safe to repeat.
    result = subprocess.run(
        [str(python), str(release / "scripts/activate-gpus.py"), "--quiet"],
        env=probe,
        capture_output=True,
        text=True,
        check=False,
    )
    summary = (result.stdout or "").strip().splitlines()
    if result.returncode:
        print("Machine activation needs attention; re-bind GPUs in Studio Settings.")
        if result.stderr.strip():
            print(result.stderr.strip()[-500:])
        return
    print(summary[0] if summary else "Machine activated.")
    for line in result.stderr.strip().splitlines():
        print(line)


bundle, prefix, state = [Path(p).expanduser().resolve() for p in sys.argv[1:4]]
prefix.mkdir(parents=True, exist_ok=True)
deployment_lock = (prefix / "upgrade.lock").open("a")
try:
    fcntl.flock(deployment_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("Another Studio upgrade or rollback is in progress")
service = sys.argv[4] == "1"
manifest = json.loads((bundle / "manifest.json").read_text())
if manifest.get("format") != "onecat-studio-linux-v1":
    raise SystemExit("Unsupported bundle")
release = prefix / "releases" / manifest["version"]
if release.exists():
    raise SystemExit("This version is already installed: " + str(release))
state.mkdir(parents=True, exist_ok=True, mode=0o700)
# No database/process migration while another management operation is in flight.
if (state / "onecat.db").is_file():
    with sqlite3.connect(state / "onecat.db") as conn:
        busy = conn.execute("SELECT count(*) FROM requests WHERE status IS NULL").fetchone()[0]
        jobs = [
            json.loads(row[0])
            for row in conn.execute("SELECT data FROM records WHERE bucket='jobs'")
        ]
        agents = [json.loads(row[0]) for row in conn.execute("SELECT data FROM records WHERE bucket='agent_tasks'")]
    if busy or any(j["state"] not in ("completed", "failed", "cancelled") for j in jobs):
        raise SystemExit("Finish or cancel active requests/tasks before upgrading")
    if any(task["state"] in {"starting", "running", "waiting", "stopping"} or task.get("settled") is False for task in agents):
        raise SystemExit("Finish or stop Agent tasks before upgrading; saved tasks can be resumed afterwards")
if service:
    subprocess.run(["systemctl", "--user", "stop", "onecat-studio.service"], capture_output=True)
previous = (prefix / "current").resolve() if (prefix / "current").exists() else None
backup = state / "backups" / ("upgrade-" + str(int(time.time())))
backup.mkdir(parents=True)
for name in ("onecat.db", "studio.db"):
    if (state / name).is_file():
        with sqlite3.connect(state / name) as src, sqlite3.connect(backup / name) as dst:
            src.backup(dst)
gpu_backup = backup / "gpu-control"
gpu_backup.mkdir()
for filename in ("/etc/onecat-studio/gpu-helper.json", "/var/lib/onecat-studio/clock-policies.json", "/var/lib/onecat-studio/pending-control.json"):
    source = Path(filename)
    if source.is_file():
        shutil.copy2(source, gpu_backup / source.name)
release.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(bundle, release, symlinks=True)
old = manifest["original_prefix"]
cfg = release / "env/pyvenv.cfg"
cfg.write_text(cfg.read_text().replace(old, str(release)))
python = release / "env/bin/python"
if python.is_symlink():
    python.unlink()
python.symlink_to(
    os.path.relpath(release / manifest["python_home"] / "bin/python3.12", python.parent)
)
for script in (release / "env/bin").iterdir():
    if script.is_file() and not script.is_symlink() and script.stat().st_size < 1024 * 1024:
        content = script.read_bytes()
        if content.startswith(b"#!"):
            script.write_bytes(content.replace(old.encode(), str(release).encode()))
launcher = release / "run"
launcher.write_text(
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    + "export ONECAT_STUDIO_HOME="
    + shlex.quote(str(state))
    + "\n"
    + "export ONECAT_STUDIO_PREFIX="
    + shlex.quote(str(prefix))
    + "\n"
    + "export PATH="
    + shlex.quote(str(release))
    + ':"$PATH"\n'
    + "export PYTHONPATH="
    + shlex.quote(str(release / "studio/backend"))
    + "\n"
    + "exec "
    + shlex.quote(str(python))
    + ' -m onecat "$@"\n'
)
launcher.chmod(0o755)
probe = {
    **os.environ,
    "PYTHONPATH": str(release / "studio/backend"),
    "ONECAT_STUDIO_HOME": str(state),
}
subprocess.run(
    [str(python), "-c", "from onecat.app import create_app; create_app()"], env=probe, check=True
)

def link(name, target):
    temporary = prefix / (name + ".new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(prefix / name)


if previous:
    link("previous", previous)
link("current", release)
(prefix / "installation.json").write_text(
    json.dumps({"state": str(state), "backup": str(backup), "service": service}, indent=2)
)
if service:
    unit = Path.home() / ".config/systemd/user/onecat-studio.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    command = (
        str(prefix / "current/run").replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    )
    unit.write_text(
        "[Unit]\nDescription=1Cat Studio inference workbench\nAfter=network.target\n\n[Service]\n"
        + 'Type=simple\nExecStart="'
        + command
        + '"\nRestart=on-failure\nRestartSec=3\nKillMode=process\nTimeoutStopSec=30\nUMask=0077\n\n[Install]\nWantedBy=default.target\n'
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "onecat-studio.service"], check=True)
print("Installed " + manifest["version"])
print("Launch: " + str(prefix / "current/run"))
print("Open http://127.0.0.1:8888 (or the saved Studio port)")
# Codex and the sandbox setup are bundled, and never use the user's Codex home.
agent_probe = subprocess.run([str(python), "-c", "from onecat.agent.runtime import info; import sys; sys.exit(0 if info()['sandbox_ready'] else 1)"], env=probe)
if agent_probe.returncode:
    subprocess.run(["bash", str(release / "scripts/install-agent-sandbox.sh")], check=False)
configure_bundled_gpu(python, release, probe)
activate_machine(python, release, probe)
