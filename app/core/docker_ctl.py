import shlex
import subprocess
from pathlib import Path

FOUNDRY_DIR = "/home/codingsheep/docker-projects/foundry"
BACKUP_DIR = "/mnt/backup-drive/foundry"
QUARTZ_DIR = "/home/codingsheep/docker-projects/obsidian/quartz"

FOUNDRY_ENV_PATH = f"{FOUNDRY_DIR}/.env"
FOUNDRY_COMPOSE_PATH = f"{FOUNDRY_DIR}/compose.yaml"

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


def get_active_version() -> str | None:
    """Read the current ACTIVE_FOUNDRY_VERSION straight from foundry/.env."""
    try:
        with open(FOUNDRY_ENV_PATH, "r") as f:
            for line in f:
                if line.startswith("ACTIVE_FOUNDRY_VERSION="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        return None
    return None


##
# Backup Functionality
##

def list_backups(version: str) -> list[str]:
    """Return available backup filenames for a version, newest first."""
    backup_path = Path(BACKUP_DIR) / version
    if not backup_path.exists():
        return []
    files = sorted(
        backup_path.glob(f"foundry-{version}-*.tar.gz"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return [f.name for f in files]


def restore_backup(version: str, filename: str) -> tuple[bool, str]:
    """Stop container, restore backup, restart container."""

    backup_path = Path(BACKUP_DIR) / version / filename
    if not backup_path.exists():
        return False, f"Backup file `{filename}` not found."

    target_dir = Path(FOUNDRY_DIR) / version

    # Validate it's actually one of our backups (basic safety check)
    if not filename.startswith(f"foundry-{version}-") or not filename.endswith(".tar.gz"):
        return False, "Invalid backup filename."

    container = f"foundry-{version}"

    # Step 1: stop container
    result = subprocess.run(
        ["docker", "stop", container],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False, f"Failed to stop `{container}`: {result.stderr.strip()}"

    # Step 2: wipe current data dir and restore from archive
    try:
        # Clear the existing data dir contents
        result = subprocess.run(
            ["rm", "-rf", str(target_dir)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to clear target dir: {result.stderr.strip()}")

        # Extract the backup (archive contains the version folder itself,
        # so extract into the parent foundry dir)
        result = subprocess.run(
            ["tar", "-xzf", str(backup_path), "-C", str(target_dir.parent)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to extract backup: {result.stderr.strip()}")

    except RuntimeError as e:
        # Try to restart the container even if restore failed — don't leave it down
        subprocess.run(["docker", "start", container], capture_output=True)
        return False, f"Restore failed (container restarted): {e}"

    # Step 3: restart container
    result = subprocess.run(
        ["docker", "start", container],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False, f"Restored successfully but failed to restart `{container}`: {result.stderr.strip()}"

    return True, f"Successfully restored `{filename}` to `{container}` and restarted."


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

    subprocess.run(
        ["docker", "restart", "nginx-proxy"],
        capture_output=True,
        text=True,
    )

    return True, "Wiki rebuilt successfully — changes are now live."