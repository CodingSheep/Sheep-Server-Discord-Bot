"""Updating a Foundry container to the newest image for its tag.

The felddy/foundryvtt image disables Foundry's built-in updater; the image tag
(e.g. `felddy/foundryvtt:14`) picks the major version, and pulling it gets the
newest build. Docker never pulls on its own, so this does it on request.
"""
import subprocess
import time

from core import config
from core.docker_ctl import backup_lock

VERSION_LABEL = "org.opencontainers.image.version"


def _run(*args: str, timeout: float = 60, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, cwd=cwd)


def _err(r: subprocess.CompletedProcess) -> str:
    return (r.stderr or r.stdout).strip()[-400:]


def _image_version(ref: str) -> str:
    r = _run("docker", "image", "inspect", "-f", f'{{{{index .Config.Labels "{VERSION_LABEL}"}}}}', ref)
    return r.stdout.strip() or "unknown" if r.returncode == 0 else "unknown"


def check(version: str) -> dict:
    """Pull the newest image for the container's tag and compare.

    Returns {ok, error, tag, current, latest, changed, running}.
    """
    container = f"foundry-{version}"
    r = _run("docker", "inspect", "-f", "{{.Config.Image}}|{{.Image}}|{{.State.Running}}", container)
    if r.returncode != 0:
        return {"ok": False, "error": f"Couldn't inspect `{container}`: {_err(r)}"}
    tag, current_id, running = r.stdout.strip().split("|")

    r = _run("docker", "compose", "pull", "-q", container, cwd=config.FOUNDRY_DIR, timeout=900)
    if r.returncode != 0:
        return {"ok": False, "error": f"Pulling `{tag}` failed: {_err(r)}"}

    r = _run("docker", "image", "inspect", "-f", "{{.Id}}", tag)
    latest_id = r.stdout.strip()
    return {
        "ok": True, "error": None, "tag": tag,
        "current": _image_version(current_id), "latest": _image_version(tag),
        "current_id": current_id, "changed": bool(latest_id) and latest_id != current_id,
        "running": running == "true",
    }


def apply(version: str, info: dict) -> tuple[bool, str]:
    """Recreate the container on the pulled image and wait for it to be healthy.

    Call only after a fresh backup. The old image is kept as `<tag>-previous`
    so it isn't pruned and a rollback stays possible.
    """
    container = f"foundry-{version}"
    repo, _, tag = info["tag"].rpartition(":")
    previous = f"{repo}:{tag}-previous"
    _run("docker", "tag", info["current_id"], previous)

    with backup_lock(timeout=600) as got:
        if not got:
            return False, "A backup has been running for 10+ minutes; nothing was changed. Try again shortly."
        args = ["docker", "compose", "up", "-d", "--no-deps", container]
        if not info["running"]:
            args = ["docker", "compose", "up", "--no-start", "--no-deps", container]
        r = _run(*args, cwd=config.FOUNDRY_DIR, timeout=300)
        if r.returncode != 0:
            return False, f"Recreating `{container}` failed: {_err(r)}"

    if not info["running"]:
        return True, (f"`{container}` was stopped, so it was recreated without starting. "
                      f"It runs **{info['latest']}** from its next start (`/foundry start {version}`).")

    # felddy's image unpacks the new release on first start, then Foundry boots.
    deadline = time.monotonic() + 300
    status = "starting"
    while time.monotonic() < deadline:
        r = _run("docker", "inspect", "-f",
                 "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}", container)
        state, _, health = r.stdout.strip().partition("|")
        status = health or state
        if state != "running" or health in ("healthy", "unhealthy"):
            break
        time.sleep(5)
    if status != "healthy":
        return False, (f"`{container}` was recreated on {info['latest']} but is **{status}**. "
                       f"`docker logs --tail 50 {container}`. Old image kept as `{previous}`.")
    return True, (f"`{container}` updated **{info['current']} → {info['latest']}** and is healthy. "
                  f"The world migrates the first time it's launched.\n"
                  f"Old image kept as `{previous}` for a rollback (see MAP.md).")
