import contextlib
import datetime
import fcntl
import re
import shutil
import subprocess
import time
from pathlib import Path

from core import backups, config

FOUNDRY_DIR = config.FOUNDRY_DIR
QUARTZ_DIR = config.QUARTZ_DIR
FOUNDRY_ENV_PATH = config.FOUNDRY_ENV_PATH
FOUNDRY_COMPOSE_PATH = config.FOUNDRY_COMPOSE_PATH

def get_container_status() -> dict[str, str]:
    """Return docker ps status for foundry-v13, foundry-v14, and nginx-proxy."""
    containers = ["foundry-v13", "foundry-v14", "nginx-proxy"]
    statuses = {}

    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"],
        capture_output=True,
        text=True,
    )

    found = {}
    for line in result.stdout.strip().splitlines():
        if "|" in line:
            name, status = line.split("|", 1)
            found[name] = status

    for c in containers:
        statuses[c] = found.get(c, "not found")

    return statuses


def start_stop(mode: str, version: str) -> tuple[bool, str]:
    """Start or stop a Foundry container directly. Doesn't touch nginx."""
    container = f"foundry-{version}"
    if mode not in ("start", "stop"):
        return False, f"Unknown mode '{mode}'"

    result = subprocess.run(
        ["docker", mode, container],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        return True, f"Container `{container}` {mode}ed."
    return False, f"Error {mode}ing `{container}`: {result.stderr.strip()}"


def switch_active_version(version: str) -> tuple[bool, str]:
    """Update ACTIVE_FOUNDRY_VERSION in foundry/.env, then recreate nginx-proxy
    so it picks up the new value."""
    target = f"foundry-{version}"

    current = get_active_version()
    if current == target:
        return False, f"`{target}` is already the active version — no change made."

    try:
        with open(FOUNDRY_ENV_PATH, "r") as f:
            lines = f.readlines()
    except OSError as e:
        return False, f"Couldn't read .env: {e}"

    found = False
    for i, line in enumerate(lines):
        if line.startswith("ACTIVE_FOUNDRY_VERSION="):
            lines[i] = f"ACTIVE_FOUNDRY_VERSION={target}\n"
            found = True
            break
    if not found:
        lines.append(f"ACTIVE_FOUNDRY_VERSION={target}\n")

    try:
        with open(FOUNDRY_ENV_PATH, "w") as f:
            f.writelines(lines)
    except OSError as e:
        return False, f"Couldn't write .env: {e}"

    result = subprocess.run(
        [
            "docker", "compose",
            "-f", FOUNDRY_COMPOSE_PATH,
            "--env-file", FOUNDRY_ENV_PATH,
            "up", "-d", "nginx-proxy",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        return True, f"Switched active version to `{target}` and restarted the proxy."
    return False, f"Switched .env to `{target}`, but restarting the proxy failed: {result.stderr.strip()}"


def _read_foundry_env(key: str) -> str | None:
    try:
        with open(FOUNDRY_ENV_PATH, "r") as f:
            for line in f:
                if line.startswith(f"{key}="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        return None
    return None


def get_active_version() -> str | None:
    """Read the current ACTIVE_FOUNDRY_VERSION straight from foundry/.env."""
    return _read_foundry_env("ACTIVE_FOUNDRY_VERSION")


def get_foundry_domain() -> str | None:
    """FOUNDRY_DOMAIN_NAME from foundry/.env (used for the cert check)."""
    return _read_foundry_env("FOUNDRY_DOMAIN_NAME")


##
# Backup Functionality
##

def list_backups(version: str) -> list[str]:
    """Backup filenames for a version, newest first (same order as /foundry backup list)."""
    return [a.filename for a in backups.archives(f"foundry-{version}")]


@contextlib.contextmanager
def backup_lock(timeout: float):
    """Hold the lock shared with the host backup script. Yields True if acquired."""
    Path(config.RUN_DIR).mkdir(parents=True, exist_ok=True)
    with open(config.BACKUP_LOCK, "a") as f:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(2)
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def restore_backup(version: str, filename: str) -> tuple[bool, str]:
    """Stop the container, set the current data aside, extract the backup, restart.

    The current data is renamed to vNN.pre-restore-<time> (instant, same disk),
    so a wrong restore can be undone. Only the newest set-aside copy is kept.
    """
    if not re.fullmatch(rf"foundry-{version}-\d{{4}}-\d{{2}}-\d{{2}}_\d{{2}}-\d{{2}}\.tar\.gz", filename):
        return False, "Invalid backup filename."

    backup_path = Path(config.BACKUP_ROOT) / "foundry" / version / filename
    if not backup_path.exists():
        return False, f"Backup file `{filename}` not found."

    target_dir = Path(FOUNDRY_DIR) / version
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    aside = target_dir.with_name(f"{version}.pre-restore-{stamp}")
    container = f"foundry-{version}"

    with backup_lock(timeout=600) as got:
        if not got:
            return False, "A backup has been running for 10+ minutes; try again shortly."

        result = subprocess.run(["docker", "stop", container], capture_output=True, text=True)
        if result.returncode != 0:
            return False, f"Failed to stop `{container}`: {result.stderr.strip()}"

        try:
            if target_dir.exists():
                target_dir.rename(aside)
            # The archive contains the version folder itself, so extract into the parent.
            result = subprocess.run(
                ["tar", "-xzf", str(backup_path), "-C", str(target_dir.parent)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"extract failed: {result.stderr.strip()[-300:]}")
            if not target_dir.is_dir():
                raise RuntimeError(f"archive did not contain a `{version}/` folder")
        except Exception as e:
            # Put the previous data back exactly as it was.
            if aside.exists():
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                aside.rename(target_dir)
            subprocess.run(["docker", "start", container], capture_output=True)
            return False, f"Restore failed; previous data put back and container restarted. ({e})"

        # Keep only the newest set-aside copy for this version.
        for old in target_dir.parent.glob(f"{version}.pre-restore-*"):
            if old != aside and old.is_dir():
                shutil.rmtree(old, ignore_errors=True)

        result = subprocess.run(["docker", "start", container], capture_output=True, text=True)

    kept = (f"Previous data kept as `{aside.name}`. Use `/foundry backup undo {version}` to switch back."
            if aside.exists() else "")
    if result.returncode != 0:
        return False, (f"Restored `{filename}` but failed to start `{container}`: "
                       f"{result.stderr.strip()}\n{kept}")
    return True, f"Restored `{filename}` to `{container}` and restarted it.\n{kept}"


def find_undo(version: str) -> Path | None:
    """The newest set-aside folder for a version (vNN.pre-restore-<time>), if any."""
    dirs = sorted(
        (d for d in Path(FOUNDRY_DIR).glob(f"{version}.pre-restore-*") if d.is_dir()),
        key=lambda d: d.name,
    )
    return dirs[-1] if dirs else None


def undo_label(folder: Path) -> str:
    """'YYYY-MM-DD HH:MM' from vNN.pre-restore-YYYY-MM-DD_HH-MM-SS."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})", folder.name)
    return f"{m.group(1)} {m.group(2)}:{m.group(3)}" if m else folder.name


def undo_restore(version: str) -> tuple[bool, str]:
    """Swap the set-aside folder back in. The data it replaces becomes the new
    set-aside copy, so running undo again switches back. Never deletes anything."""
    aside = find_undo(version)
    if aside is None:
        return False, f"Nothing to undo for `{version}` (no set-aside folder from a restore)."

    target_dir = Path(FOUNDRY_DIR) / version
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    new_aside = target_dir.with_name(f"{version}.pre-restore-{stamp}")
    container = f"foundry-{version}"
    from_label = undo_label(aside)

    with backup_lock(timeout=600) as got:
        if not got:
            return False, "A backup has been running for 10+ minutes; try again shortly."

        result = subprocess.run(["docker", "stop", container], capture_output=True, text=True)
        if result.returncode != 0:
            return False, f"Failed to stop `{container}`: {result.stderr.strip()}"

        try:
            if target_dir.exists():
                target_dir.rename(new_aside)
            aside.rename(target_dir)
        except OSError as e:
            # Put things back the way they were.
            if not target_dir.exists() and new_aside.exists():
                new_aside.rename(target_dir)
            subprocess.run(["docker", "start", container], capture_output=True)
            return False, f"Undo failed; nothing changed and the container was restarted. ({e})"

        result = subprocess.run(["docker", "start", container], capture_output=True, text=True)

    switch_back = (f"The replaced data is kept as `{new_aside.name}`; run undo again to switch back."
                   if new_aside.exists() else "")
    if result.returncode != 0:
        return False, (f"Swapped in the data set aside at {from_label}, but failed to start "
                       f"`{container}`: {result.stderr.strip()}\n{switch_back}")
    return True, (f"`{container}` is back to the data that was set aside at {from_label} "
                  f"and has been restarted.\n{switch_back}")


def get_foundry_dir() -> str:
    return FOUNDRY_DIR

##
# WIKI
##

def build_wiki() -> tuple[bool, str]:
    """Trigger a Quartz rebuild of the player wiki."""
    result = subprocess.run(
        ["npx", "quartz", "build"],
        capture_output=True,
        text=True,
        cwd=QUARTZ_DIR,
    )

    if result.returncode != 0:
        return False, f"Wiki build failed:\n```{result.stderr.strip()[-500:]}```"

    subprocess.run(
        ["chmod", "-R", "755", f"{QUARTZ_DIR}/public"],
        capture_output=True,
        text=True,
    )

    # nginx serves public/ straight from disk, so no restart is needed.
    return True, "Wiki rebuilt successfully — changes are now live."