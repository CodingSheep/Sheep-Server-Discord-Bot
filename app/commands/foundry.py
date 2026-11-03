import asyncio

import discord
from discord import app_commands

from commands.backup import BackupGroup, run_backup
from commands.wiki import WikiGroup
from core import updates
from core.auth import is_allowed
from core.docker_ctl import (
    start_stop, switch_active_version, get_active_version, get_container_status
)

VERSION_CHOICES = [
    app_commands.Choice(name="v13", value="v13"),
    app_commands.Choice(name="v14", value="v14"),
]

_update_lock = asyncio.Lock()


class UpdateConfirm(discord.ui.View):
    """Update / Cancel buttons for /foundry update. Only the allowed user can press them."""

    def __init__(self, version: str, info: dict):
        super().__init__(timeout=300)
        self.version = version
        self.info = info
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_allowed(interaction.user.id):
            await interaction.response.send_message("You're not authorized to do that.", ephemeral=True)
            return False
        return True

    async def _close(self, interaction: discord.Interaction, text: str):
        self.stop()
        await interaction.response.edit_message(content=text, embed=None, view=None)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="Update not confirmed in 5 minutes; nothing was changed.",
                                        embed=None, view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Update", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if _update_lock.locked():
            await interaction.response.send_message("An update is already running.", ephemeral=True)
            return
        container = f"foundry-{self.version}"
        await self._close(interaction, f"🔄 Updating `{container}`: backing up first...")
        async with _update_lock:
            st, problem = await run_backup(True, [container])
            result = (st or {}).get("results", {}).get(container, {})
            if problem or not st.get("ok") or result.get("status") != "saved":
                why = problem or st.get("error") or result.get("detail") or "backup didn't save"
                await interaction.followup.send(f"❌ Backup failed, so the update was cancelled. Nothing changed.\n{why}")
                return
            await interaction.followup.send(
                f"✅ Backed up: `{result.get('detail', '')}`. Recreating `{container}` "
                f"(up to a few minutes)...")
            ok, msg = await asyncio.to_thread(updates.apply, self.version, self.info)
        await interaction.followup.send(("✅ " if ok else "❌ ") + msg)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._close(interaction, "Update cancelled; nothing was changed.")


class FoundryGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="foundry", description="Manage the Foundry VTT server")
        self.add_command(BackupGroup())
        self.add_command(WikiGroup())

    async def _check_auth(self, interaction: discord.Interaction) -> bool:
        if not is_allowed(interaction.user.id):
            await interaction.response.send_message(
                "You're not authorized to use this command.", ephemeral=True
            )
            return False
        return True

    @app_commands.command(name="status", description="Get the current Foundry status")
    async def status(self, interaction: discord.Interaction):
        active = await asyncio.to_thread(get_active_version)
        statuses = await asyncio.to_thread(get_container_status)

        embed = discord.Embed(title="FoundryVTT Status")
        embed.add_field(name="Active Version", value=active or "unknown", inline=False)
        embed.add_field(name=f"Container Statuses", value="\n".join([f"{name:<12}  -  {stat}" for name, stat in statuses.items()]), inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="start", description="Start a Foundry version")
    @app_commands.describe(version="The Foundry version to start")
    @app_commands.choices(version=VERSION_CHOICES)
    async def start(self, interaction: discord.Interaction, version: app_commands.Choice[str]):
        if not await self._check_auth(interaction):
            return
        await interaction.response.defer()  # docker start can take a few seconds
        ok, message = await asyncio.to_thread(start_stop, "start", version.value)
        await interaction.followup.send(message, ephemeral=not ok)

    @app_commands.command(name="stop", description="Stop a Foundry version")
    @app_commands.describe(version="The Foundry version to stop")
    @app_commands.choices(version=VERSION_CHOICES)
    async def stop(self, interaction: discord.Interaction, version: app_commands.Choice[str]):
        if not await self._check_auth(interaction):
            return
        await interaction.response.defer()  # docker stop waits for Foundry to shut down
        ok, message = await asyncio.to_thread(start_stop, "stop", version.value)
        await interaction.followup.send(message, ephemeral=not ok)

    @app_commands.command(name="switch", description="Switch the active Foundry version")
    @app_commands.describe(version="The Foundry version to make active")
    @app_commands.choices(version=VERSION_CHOICES)
    async def switch(self, interaction: discord.Interaction, version: app_commands.Choice[str]):
        if not await self._check_auth(interaction):
            return
        await interaction.response.defer()  # restarting nginx takes a moment
        ok, message = await asyncio.to_thread(switch_active_version, version.value)
        await interaction.followup.send(message)

    @app_commands.command(name="update", description="Update a Foundry version to the newest build (asks first)")
    @app_commands.describe(version="The Foundry version to update")
    @app_commands.choices(version=VERSION_CHOICES)
    async def update(self, interaction: discord.Interaction, version: app_commands.Choice[str]):
        if not await self._check_auth(interaction):
            return
        await interaction.response.defer()  # pulling the image can take a minute
        info = await asyncio.to_thread(updates.check, version.value)
        if not info["ok"]:
            await interaction.followup.send(f"❌ {info['error']}")
            return
        if not info["changed"]:
            await interaction.followup.send(
                f"`foundry-{version.value}` is up to date: **{info['current']}** (`{info['tag']}`).")
            return

        embed = discord.Embed(
            title=f"Update foundry-{version.value}?",
            description=(f"**{info['current']} → {info['latest']}**\n\n"
                         f"• Takes a fresh backup first, and stops if it fails.\n"
                         f"• {'Restarts the container (about a minute of downtime).' if info['running'] else 'The container is stopped; it gets the new build at its next start.'}\n"
                         f"• Foundry migrates the world when it's next launched. That can't be undone "
                         f"except by restoring the backup on the old image."),
            color=discord.Color.orange(),
        )
        view = UpdateConfirm(version.value, info)
        view.message = await interaction.followup.send(embed=embed, view=view, wait=True)
