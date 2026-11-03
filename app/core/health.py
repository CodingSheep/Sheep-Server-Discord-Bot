"""Checks for things that can fail quietly: containers, disks, error lines in
container logs, and failed systemd units on the host.

Each check takes the shared alert state dict, updates it, and returns messages
to DM. The Monitor cog (monitor.py) runs them on a timer.
"""
import datetime
import json
import os
import re
import subprocess
import time

from core import backups, config

# How many 60 s checks a container problem must last before it alerts. Rides
# out recreates (/foundry switch) and the host shutting down.
CONTAINER_CHECKS_BEFORE_ALERT = 2
HOST_STATUS_STALE_SEC = 20 * 60
# A kind of log error that hasn't been seen for this long alerts again.
LOG_SIG_FORGET_SEC = 7 * 86400
LOG_SIG_MAX = 1000
LOG_MAX_WINDOW_SEC = 86400


def transition(state: dict, key: str, problem: str | None, kind: str | None,
               what: str) -> str | None:
    """Alert once when a problem starts (or its kind changes), once when it clears."""
    alerts = state.setdefault("alerting", {})
    if problem:
        if alerts.get(key) == kind:
            return None
        alerts[key] = kind
        return f"⚠️ **{what}**\n{problem}"
    if key in alerts:
        del alerts[key]
        return f"✅ **{what}: back to normal.**"
    return None


# ---- containers -------------------------------------------------------------

def inspect_containers() -> list[dict]:
    ids = subprocess.run(["docker", "ps", "-aq"], capture_output=True, text=True,
                         timeout=30, check=True).stdout.split()
    if not ids:
        return []
    r = subprocess.run(["docker", "inspect", *ids], capture_output=True, text=True, timeout=30)
    # A container removed between the two calls makes inspect exit 1 but still
    # print the others.
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError((r.stderr or "docker inspect failed").strip()[-300:])


def _name(c: dict) -> str:
    return c["Name"].lstrip("/")


def container_problems(containers: list[dict]) -> dict[str, tuple[str, str]]:
    """name -> (kind, description) for each container that needs attention."""
    out = {}
    for c in containers:
        name, s = _name(c), c["State"]
        status = s.get("Status")
        health = s.get("Health") or {}
        if status == "restarting":
            out[name] = ("restarting", "keeps crashing and restarting. "
                         f"`docker logs --tail 50 {name}`")
        elif health.get("Status") == "unhealthy":
            last = ((health.get("Log") or [{}])[-1].get("Output") or "").strip()[:300]
            out[name] = ("unhealthy", "is running but failing its healthcheck."
                         + (f"\n`{last}`" if last else ""))
        elif name in config.ALWAYS_ON and status != "running":
            why = "killed: out of memory" if s.get("OOMKilled") else f"exit code {s.get('ExitCode')}"
            if s.get("Error"):
                why += f", {s['Error'][:200]}"
            out[name] = ("stopped", f"is {status} ({why}). "
                         f"Start it with `docker compose up -d` in its project folder.")
    names = {_name(c) for c in containers}
    for name in config.ALWAYS_ON - names:
        out[name] = ("missing", "doesn't exist (removed by `docker compose down`?).")
    return out


def check_containers(state: dict, containers: list[dict]) -> list[str]:
    msgs = []
    problems = container_problems(containers)
    pending = state.setdefault("container_pending", {})
    for name in set(pending) - set(problems):
        del pending[name]
    for name, (kind, desc) in problems.items():
        pending[name] = pending.get(name, 0) + 1
        if pending[name] >= CONTAINER_CHECKS_BEFORE_ALERT:
            m = transition(state, f"container:{name}", f"`{name}` {desc}", kind,
                           f"Container {name}")
            if m:
                msgs.append(m)
    # Recoveries for containers that were alerting and are fine now.
    for key in [k for k in state.get("alerting", {}) if k.startswith("container:")]:
        name = key.split(":", 1)[1]
        if name not in problems:
            m = transition(state, key, None, None, f"Container {name}")
            if m:
                msgs.append(m)
    return msgs


def check_restarts(state: dict, containers: list[dict]) -> list[str]:
    """Docker's restart policy hides crashes; report each one.

    RestartCount only counts restarts by the policy (not `docker restart`, boots
    or recreates), so any increase is a crash.
    """
    prev = state.get("restart_counts", {})
    now = {}
    msgs = []
    for c in containers:
        name, cid, n = _name(c), c["Id"][:12], c.get("RestartCount", 0)
        now[name] = [cid, n]
        old = prev.get(name)
        if old and old[0] == cid and n > old[1]:
            times = n - old[1]
            msgs.append(f"🔁 **`{name}` crashed and Docker restarted it**"
                        f"{f' ({times} times)' if times > 1 else ''}.\n"
                        f"`docker logs --tail 50 {name}`")
    state["restart_counts"] = now
    return msgs


# ---- disks ------------------------------------------------------------------

def check_disks(state: dict) -> list[str]:
    msgs = []
    try:
        server_dev = os.stat(config.FOUNDRY_DIR).st_dev
    except OSError:
        server_dev = None
    for label, path in (("Server disk", config.FOUNDRY_DIR), ("Backup drive", config.BACKUP_ROOT)):
        try:
            if label == "Backup drive" and os.stat(path).st_dev == server_dev:
                continue   # drive not mounted; the backup script alerts about that
            st = os.statvfs(path)
        except OSError:
            continue
        used = st.f_blocks - st.f_bfree
        pct = 100 * used / max(used + st.f_bavail, 1)
        free_gb = st.f_bavail * st.f_frsize / 1e9
        problem = (f"{pct:.0f}% full, {free_gb:.1f} GB free (alert at {config.DISK_WARN_PCT}%)."
                   if pct >= config.DISK_WARN_PCT else None)
        m = transition(state, f"disk:{label}", problem, "full" if problem else None,
                       f"{label} space")
        if m:
            msgs.append(m)
    return msgs


# ---- host (systemd) ---------------------------------------------------------

def check_host(state: dict, bot_started: float) -> list[str]:
    """Failed systemd units, from host-status.json (written by server-watchdog.sh)."""
    st = backups.read_json(config.HOST_STATUS)
    if not st:
        return []   # watchdog not installed yet
    msgs = []
    try:
        age = time.time() - datetime.datetime.fromisoformat(st["time"]).timestamp()
    except (KeyError, ValueError):
        age = 0
    stale = None
    if age > HOST_STATUS_STALE_SEC and time.time() - bot_started > HOST_STATUS_STALE_SEC:
        stale = (f"The host watchdog hasn't reported for {age / 60:.0f} min, so failed services "
                 f"and bot outages aren't being watched.\n`systemctl status server-watchdog.timer`")
    m = transition(state, "host:watchdog", stale, "stale" if stale else None, "Host watchdog")
    if m:
        msgs.append(m)

    failed = sorted(st.get("failed_units") or [])
    problem = None
    if failed:
        lines = "\n".join(f"• `{u}`" for u in failed[:15])
        problem = (f"{lines}\nDetails: `systemctl status <unit>` or `journalctl -u <unit> -n 30`. "
                   f"Clear a fixed one with `sudo systemctl reset-failed <unit>`.")
    m = transition(state, "host:failed_units", problem, ",".join(failed) or None,
                   "Failed services on the host")
    if m:
        msgs.append(m)
    return msgs


# ---- container logs ---------------------------------------------------------

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
ACCESS_LOG = re.compile(r'"(GET|POST|HEAD|PUT|DELETE|OPTIONS|PATCH|PROPFIND|CONNECT) \S* HTTP/')
QUIET_LEVEL = re.compile(r"\[(info|debug|notice|warn|warning)\]", re.I)
ERROR_LINE = re.compile(
    r"\[(error|err|crit|critical|alert|emerg|fatal)\]"     # nginx, couchdb, foundry
    r"|\b(FATAL|PANIC)\b"
    r"|^\s*([A-Za-z_][\w.]*)?(Error|Exception)\b:")         # Python / Node exceptions
TRACEBACK = "Traceback (most recent call last)"


def error_lines(text: str) -> list[str]:
    """Error-looking lines from a block of container log output."""
    out = []
    in_tb = False
    for raw in text.splitlines():
        line = ANSI.sub("", raw).rstrip()
        if not line:
            continue
        if TRACEBACK in line:
            in_tb = True
            continue
        if in_tb:
            # The exception itself is the first unindented line after the frames.
            if line[0].isspace() or line.startswith(("During handling", "The above exception")):
                continue
            in_tb = False
            out.append(line)
            continue
        if ACCESS_LOG.search(line) or QUIET_LEVEL.search(line):
            continue
        if ERROR_LINE.search(line):
            out.append(line)
    return out


def signature(line: str) -> str:
    """The line with times, IPs, IDs, numbers and quoted values blanked, so
    repeats of the same error match."""
    s = re.sub(r"\d{4}-\d\d-\d\d[T ][\d:.,]+(Z|[+-]\d\d:?\d\d)?", "", line)
    s = re.sub(r"\b\d{1,3}(\.\d{1,3}){3}(:\d+)?\b", "<ip>", s)
    s = re.sub(r"\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{8,}\b", "<id>", s)
    s = re.sub(r"\d+", "#", s)
    s = re.sub(r"\"[^\"]*\"|'[^']*'", "'…'", s)
    return re.sub(r"\s+", " ", s).strip()[:160]


def check_logs(state: dict, containers: list[dict]) -> list[str]:
    """DM new kinds of error lines from container logs since the last check.

    A kind that keeps repeating (Foundry's gameplay errors, say) alerts once,
    then again only after a week without it.
    """
    now = time.time()
    since = state.get("log_since")
    state["log_since"] = now
    if since is None:
        return []   # first run: start from now, not from old history
    since = max(since, now - LOG_MAX_WINDOW_SEC)

    sigs = state.setdefault("log_sigs", {})
    found = {}   # container -> new lines
    for c in containers:
        name = _name(c)
        r = subprocess.run(["docker", "logs", "--since", f"{since:.3f}", "--until", f"{now:.3f}", name],
                           capture_output=True, text=True, timeout=60, errors="replace")
        for line in error_lines(r.stdout + "\n" + r.stderr):
            key = f"{name}|{signature(line)}"
            last = sigs.get(key)
            sigs[key] = now
            if last is None or now - last > LOG_SIG_FORGET_SEC:
                lines = found.setdefault(name, [])
                if key not in {f"{name}|{signature(x)}" for x in lines}:
                    lines.append(line)
    if len(sigs) > LOG_SIG_MAX:
        for k in sorted(sigs, key=sigs.get)[:len(sigs) - LOG_SIG_MAX]:
            del sigs[k]

    msgs = []
    for name, lines in found.items():
        shown = "\n".join(l[:300] for l in lines[:4])
        more = f"(+{len(lines) - 4} more kinds)\n" if len(lines) > 4 else ""
        msgs.append(f"📝 **New error in `{name}` logs**\n```\n{shown}\n```\n{more}"
                    f"More: `docker logs --since 10m {name}`. Each kind of error is reported "
                    f"once, and again if it comes back after a week.")
    return msgs
