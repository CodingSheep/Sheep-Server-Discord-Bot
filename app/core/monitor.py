"""Background checks that DM the allowed user: backup failures, DDNS failures,
and a certificate close to expiry.

Alerts fire when something starts failing (or the error changes), plus one
"recovered" message when it's fixed. State is saved in RUN_DIR so a bot restart
doesn't repeat old alerts.
"""
import asyncio
import json
import os
import socket
import ssl
import subprocess
import time
import traceback

from discord.ext import commands, tasks

from core import backups, config
from core.auth import ALLOWED_USER_ID
from core.docker_ctl import get_foundry_domain


def _load_state() -> dict:
    return backups.read_json(config.ALERT_STATE) or {}


def _save_state(state: dict) -> None:
    try:
        tmp = config.ALERT_STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, config.ALERT_STATE)
    except OSError:
        traceback.print_exc()


def evaluate(state: dict, key: str, run_id: str | None, ok: bool | None, error: str | None,
             what: str, notify: bool = True) -> str | None:
    """Update state for one check; return a message to send, or None.

    run_id identifies a finished run (its timestamp); the same run is only evaluated once.
    """
    if run_id is None or ok is None or state.get(f"{key}_seen") == run_id:
        return None
    state[f"{key}_seen"] = run_id
    failing = state.get(f"{key}_failing", False)
    if not ok:
        error = error or "unknown error"
        changed = not failing or state.get(f"{key}_error") != error
        state[f"{key}_failing"] = True
        state[f"{key}_error"] = error
        if changed and notify:
            return f"⚠️ **{what} failed**\n{error}"
        return None
    if failing:
        state[f"{key}_failing"] = False
        state.pop(f"{key}_error", None)
        return f"✅ **{what} is working again.**" if notify else None
    return None


def check_status_files(state: dict) -> list[str]:
    msgs = []

    st = backups.read_status()
    if st and st.get("state") == "finished":
        error = st.get("error")
        failed = [n for n, r in (st.get("results") or {}).items() if r.get("status") == "failed"]
        if failed:
            details = "\n".join(f"• {n}: {st['results'][n].get('detail', '')}" for n in failed)
            error = f"{error or 'Backup failed'}\n{details}"
        # Discord-triggered runs already got a reply; just track their state.
        m = evaluate(state, "backup", st.get("finished"), st.get("ok"), error,
                     "Backup", notify=st.get("trigger") != "discord")
        if m:
            msgs.append(m + "\nDetails: `journalctl -u server-backup -n 30`")

    dd = backups.read_json(config.DDNS_STATUS)
    if dd:
        m = evaluate(state, "ddns", dd.get("time"), dd.get("ok"), dd.get("error"), "DDNS update")
        if m:
            msgs.append(m)
    return msgs


def cert_days_left(host: str, name: str, timeout: float = 10) -> float:
    """Days until the cert nginx serves for `name` expires. Raises if it doesn't verify."""
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=name) as tls:
            not_after = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
    return (not_after - time.time()) / 86400


def check_cert(state: dict) -> str | None:
    """Return an alert if the cert is close to expiry or broken; at most once a day."""
    name = get_foundry_domain()
    if not name:
        return None
    try:
        days = cert_days_left(config.CERT_CHECK_HOST, name)
        problem = (f"Certificate for `{name}` expires in {days:.0f} days. certbot should renew "
                   f"at 30 days left, so renewal looks broken: `docker logs certbot`."
                   if days < config.CERT_WARN_DAYS else None)
    except (OSError, ssl.SSLError) as e:
        problem = f"Couldn't verify the certificate for `{name}`: {e}"

    if problem is None:
        state.pop("cert_alerted", None)
        return None
    today = time.strftime("%Y-%m-%d")
    if state.get("cert_alerted") == today:
        return None
    state["cert_alerted"] = today
    return f"⚠️ **Certificate problem**\n{problem}"


DOCKER_SOCK = "/var/run/docker.sock"


def check_docker() -> tuple[bool, str | None]:
    """Can the bot talk to Docker? If not, say why and how to fix it."""
    try:
        r = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return True, None
        err = (r.stderr or r.stdout).strip()[-300:]
    except (OSError, subprocess.TimeoutExpired) as e:
        err = str(e)
    hint = ""
    try:
        sock_gid = os.stat(DOCKER_SOCK).st_gid
        groups = sorted(set(os.getgroups()) | {os.getgid()})
        if sock_gid not in groups:
            hint = (f"\nThe Docker socket belongs to group **{sock_gid}**, but the bot runs with groups {groups}. "
                    f"The docker group ID probably changed. On the host, set `group_add` to `\"{sock_gid}\"` in "
                    f"`discord-bot/compose.yaml`, then run `docker compose up -d` in `discord-bot/`.")
    except FileNotFoundError:
        hint = f"\nThe Docker socket isn't mounted at `{DOCKER_SOCK}` (check `discord-bot/compose.yaml`)."
    return False, f"Start/stop/switch/restore won't work.\n`{err}`{hint}"


class Monitor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = _load_state()
        self.lock = asyncio.Lock()   # both loops share self.state
        self.status_loop.start()
        self.cert_loop.start()
        self.docker_loop.start()

    async def cog_unload(self):
        self.status_loop.cancel()
        self.cert_loop.cancel()
        self.docker_loop.cancel()

    async def _dm(self, text: str) -> None:
        try:
            user = self.bot.get_user(int(ALLOWED_USER_ID)) or await self.bot.fetch_user(int(ALLOWED_USER_ID))
            await user.send(text[:1900])
        except Exception:
            traceback.print_exc()

    @tasks.loop(seconds=60)
    async def status_loop(self):
        try:
            async with self.lock:
                msgs = await asyncio.to_thread(check_status_files, self.state)
                await asyncio.to_thread(_save_state, self.state)
            for m in msgs:
                await self._dm(m)
        except Exception:
            traceback.print_exc()

    @tasks.loop(hours=24)
    async def cert_loop(self):
        try:
            async with self.lock:
                msg = await asyncio.to_thread(check_cert, self.state)
                await asyncio.to_thread(_save_state, self.state)
            if msg:
                await self._dm(msg)
        except Exception:
            traceback.print_exc()

    @tasks.loop(hours=1)
    async def docker_loop(self):
        """Runs at startup, then hourly; DMs when Docker access breaks and when it's back."""
        try:
            ok, error = await asyncio.to_thread(check_docker)
            async with self.lock:
                msg = evaluate(self.state, "docker", str(int(time.time())), ok, error, "Docker access")
                await asyncio.to_thread(_save_state, self.state)
            if msg:
                await self._dm(msg)
        except Exception:
            traceback.print_exc()

    @status_loop.before_loop
    @cert_loop.before_loop
    @docker_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()
