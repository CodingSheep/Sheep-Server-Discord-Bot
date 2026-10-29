import asyncio
import shutil
import time
from typing import Optional

import discord
from discord import app_commands

from core import backups, config
from core.auth import is_allowed
from core.docker_ctl import find_undo, list_backups, restore_backup, undo_label, undo_restore

VERSION_CHOICES = [
    app_commands.Choice(name="v13", value="v13"),
    app_commands.Choice(name="v14", value="v14"),
]

SOURCE_CHOICES = [app_commands.Choice(name="All sources", value="all")] + [
    app_commands.Choice(name=label, value=name) for name, (_, label) in backups.SOURCES.items()
]

STATUS_ICON = {"saved": "✅", "unchanged": "➖", "failed": "❌"}

# Discord interaction tokens expire after 15 minutes.
NOW_START_TIMEOUT = 10 * 60
NOW_TOTAL_TIMEOUT = 13 * 60


def _ts(epoch: int, style: str = "f") -> str:
    """Discord timestamp: shown in the viewer's own time zone."""
    return f"<t:{int(epoch)}:{style}>"


def _parse_iso(value: str | None) -> int | None:
    if not value:
        return None
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return None


class BackupGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="backup", description="Manage server backups")
        self._now_lock = asyncio.Lock()

    async def _check_auth(self, interaction: discord.Interaction) -> bool:
        if not is_allowed(interaction.user.id):
            await interaction.response.send_message(
                "You're not authorized to use this command.", ephemeral=True
            )
            return False
        return True

    # ---------- list ----------

    @app_commands.command(name="list", description="List available backups for a Foundry version")
    @app_commands.describe(version="The Foundry version to list backups for")
    @app_commands.choices(version=VERSION_CHOICES)
    async def list_cmd(self, interaction: discord.Interaction, version: app_commands.Choice[str]):
        if not await self._check_auth(interaction):
            return

        items = await asyncio.to_thread(backups.archives, f"foundry-{version.value}")
        if not items:
            await interaction.response.send_message(
                f"No backups found for `{version.value}`.", ephemeral=True
            )
            return

        lines = [
            f"`{i+1}.` {a.label} · {backups.human_size(a.size)} · {_ts(a.mtime, 'R')} · *{'+'.join(a.tiers)}*"
            for i, a in enumerate(items)
        ]
        embed = discord.Embed(
            title=f"Backups — foundry-{version.value}",
            description="\n".join(lines),
        )
        embed.set_footer(text=f"Use /foundry backup restore {version.value} <number> to restore")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------- restore ----------

    @app_commands.command(name="restore", description="Restore a Foundry backup by number from the list")
    @app_commands.describe(
        version="The Foundry version to restore",
        number="Backup number from /foundry backup list"
    )
    @app_commands.choices(version=VERSION_CHOICES)
    async def restore_cmd(self, interaction: discord.Interaction, version: app_commands.Choice[str], number: int):
        if not await self._check_auth(interaction):
            return

        names = await asyncio.to_thread(list_backups, version.value)
        if not names:
            await interaction.response.send_message(
                f"No backups found for `{version.value}`.", ephemeral=True
            )
            return

        if number < 1 or number > len(names):
            await interaction.response.send_message(
                f"Invalid number — pick between 1 and {len(names)}.", ephemeral=True
            )
            return

        filename = names[number - 1]
        await interaction.response.defer()
        ok, message = await asyncio.to_thread(restore_backup, version.value, filename)
        await interaction.followup.send(message)

    # ---------- undo ----------

    @app_commands.command(name="undo", description="Undo the last restore (swap the set-aside data back in)")
    @app_commands.describe(version="The Foundry version to undo")
    @app_commands.choices(version=VERSION_CHOICES)
    async def undo_cmd(self, interaction: discord.Interaction, version: app_commands.Choice[str]):
        if not await self._check_auth(interaction):
            return
        await interaction.response.defer()
        ok, message = await asyncio.to_thread(undo_restore, version.value)
        await interaction.followup.send(message)

    # ---------- now ----------

    @app_commands.command(name="now", description="Back up now (unchanged sources are skipped unless force is set)")
    @app_commands.describe(source="What to back up (default: all)", force="Back up even if nothing changed")
    @app_commands.choices(source=SOURCE_CHOICES)
    async def now_cmd(self, interaction: discord.Interaction,
                      source: Optional[app_commands.Choice[str]] = None, force: bool = False):
        if not await self._check_auth(interaction):
            return
        if self._now_lock.locked():
            await interaction.response.send_message("A backup request is already in progress.", ephemeral=True)
            return

        async with self._now_lock:
            await interaction.response.defer()
            only = [] if source is None or source.value == "all" else [source.value]
            rid = await asyncio.to_thread(backups.request_backup, force, only)

            started = False
            t0 = time.monotonic()
            st = None
            while True:
                await asyncio.sleep(3)
                st = await asyncio.to_thread(backups.read_status)
                if st and st.get("request_id") == rid:
                    if st.get("state") == "finished":
                        break
                    started = True
                elapsed = time.monotonic() - t0
                if not started and elapsed > NOW_START_TIMEOUT:
                    await interaction.followup.send(
                        "Request queued, but no backup has started after 10 minutes. "
                        "Check that `server-backup.path` is installed and enabled on the host."
                    )
                    return
                if elapsed > NOW_TOTAL_TIMEOUT:
                    await interaction.followup.send(
                        "Backup is still running. Check `/foundry backup status` in a few minutes."
                    )
                    return

            await interaction.followup.send(embed=self._result_embed(st))

    @staticmethod
    def _result_embed(st: dict) -> discord.Embed:
        ok = st.get("ok")
        embed = discord.Embed(
            title="Backup finished" if ok else "Backup failed",
            color=discord.Color.green() if ok else discord.Color.red(),
        )
        lines = []
        for name, r in (st.get("results") or {}).items():
            icon = STATUS_ICON.get(r.get("status"), "•")
            label = backups.SOURCES.get(name, (None, name))[1]
            detail = r.get("detail", "")
            if r.get("status") == "unchanged":
                detail = f"unchanged since {detail}"
            lines.append(f"{icon} **{label}** — {detail}")
        if lines:
            embed.description = "\n".join(lines)
        if st.get("error"):
            embed.add_field(name="Error", value=st["error"][:1000], inline=False)
        return embed

    # ---------- status ----------

    @app_commands.command(name="status", description="Latest backups, last run, and free space")
    async def status_cmd(self, interaction: discord.Interaction):
        if not await self._check_auth(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        embed = await asyncio.to_thread(self._status_embed)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @staticmethod
    def _status_embed() -> discord.Embed:
        embed = discord.Embed(title="Backup status")

        for name, (_, label) in backups.SOURCES.items():
            items = backups.archives(name)
            if items:
                a = items[0]
                total = sum(x.size for x in items)
                value = (f"Latest {_ts(a.mtime)} ({_ts(a.mtime, 'R')}) · {backups.human_size(a.size)}\n"
                         f"{len(items)} kept · {backups.human_size(total)} total")
            else:
                value = "No backups yet"
            embed.add_field(name=label, value=value, inline=False)

        st = backups.read_status()
        if st:
            if st.get("state") == "running":
                run = f"🔄 Running since {_ts(_parse_iso(st.get('started')) or 0, 'R')}"
            else:
                when = _parse_iso(st.get("finished"))
                icon = "✅" if st.get("ok") else "❌"
                run = f"{icon} {'OK' if st.get('ok') else 'Failed'} {_ts(when, 'R') if when else ''} ({st.get('trigger')})"
                if st.get("error"):
                    run += f"\n{st['error'][:300]}"
            nxt = st.get("next_timer_epoch")
            if nxt and nxt > time.time():
                run += f"\nNext check {_ts(nxt, 'R')}"
            embed.add_field(name="Last run", value=run, inline=False)
        else:
            embed.add_field(name="Last run", value="No status yet (host script not updated?)", inline=False)

        undo = []
        for v in ("v13", "v14"):
            folder = find_undo(v)
            if folder:
                undo.append(f"`{v}`: data set aside at {undo_label(folder)} · `/foundry backup undo {v}`")
        if undo:
            embed.add_field(name="Undo available", value="\n".join(undo), inline=False)

        try:
            u = shutil.disk_usage(config.BACKUP_ROOT)
            embed.set_footer(text=f"Backup drive: {backups.human_size(u.free)} free of {backups.human_size(u.total)}")
        except OSError:
            embed.set_footer(text="Backup drive: not readable")
        return embed
