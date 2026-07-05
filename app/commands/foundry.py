import discord
from discord import app_commands

from commands.backup import BackupGroup
from commands.wiki import WikiGroup
from core.auth import is_allowed
from core.docker_ctl import (
    start_stop, switch_active_version, get_active_version, get_container_status
)

VERSION_CHOICES = [
    app_commands.Choice(name="v13", value="v13"),
    app_commands.Choice(name="v14", value="v14"),
]

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
        active = get_active_version()
        statuses = get_container_status()

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
        ok, message = start_stop("start", version.value)
        await interaction.response.send_message(message, ephemeral=not ok)

    @app_commands.command(name="stop", description="Stop a Foundry version")
    @app_commands.describe(version="The Foundry version to stop")
    @app_commands.choices(version=VERSION_CHOICES)
    async def stop(self, interaction: discord.Interaction, version: app_commands.Choice[str]):
        if not await self._check_auth(interaction):
            return
        ok, message = start_stop("stop", version.value)
        await interaction.response.send_message(message, ephemeral=not ok)

    @app_commands.command(name="switch", description="Switch the active Foundry version")
    @app_commands.describe(version="The Foundry version to make active")
    @app_commands.choices(version=VERSION_CHOICES)
    async def switch(self, interaction: discord.Interaction, version: app_commands.Choice[str]):
        if not await self._check_auth(interaction):
            return
        await interaction.response.defer()  # restarting nginx takes a moment
        ok, message = switch_active_version(version.value)
        await interaction.followup.send(message)