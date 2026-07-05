import discord
from discord import app_commands

from core.auth import is_allowed
from core.docker_ctl import list_backups, restore_backup

VERSION_CHOICES = [
    app_commands.Choice(name="v13", value="v13"),
    app_commands.Choice(name="v14", value="v14"),
]

class BackupGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="backup", description="Manage Foundry backups")

    async def _check_auth(self, interaction: discord.Interaction) -> bool:
        if not is_allowed(interaction.user.id):
            await interaction.response.send_message(
                "You're not authorized to use this command.", ephemeral=True
            )
            return False
        return True

    @app_commands.command(name="list", description="List available backups for a Foundry version")
    @app_commands.describe(version="The Foundry version to list backups for")
    @app_commands.choices(version=VERSION_CHOICES)
    async def list_cmd(self, interaction: discord.Interaction, version: app_commands.Choice[str]):
        if not await self._check_auth(interaction):
            return

        backups = list_backups(version.value)
        if not backups:
            await interaction.response.send_message(
                f"No backups found for `{version.value}`.", ephemeral=True
            )
            return

        lines = [f"`{i+1}.` {name}" for i, name in enumerate(backups)]
        embed = discord.Embed(
            title=f"Backups — foundry-{version.value}",
            description="\n".join(lines),
        )
        embed.set_footer(text=f"Use /foundry backup restore {version.value} <number> to restore")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="restore", description="Restore a Foundry backup by number from the list")
    @app_commands.describe(
        version="The Foundry version to restore",
        number="Backup number from /foundry backup list"
    )
    @app_commands.choices(version=VERSION_CHOICES)
    async def restore_cmd(self, interaction: discord.Interaction, version: app_commands.Choice[str], number: int):
        if not await self._check_auth(interaction):
            return

        backups = list_backups(version.value)
        if not backups:
            await interaction.response.send_message(
                f"No backups found for `{version.value}`.", ephemeral=True
            )
            return

        if number < 1 or number > len(backups):
            await interaction.response.send_message(
                f"Invalid number — pick between 1 and {len(backups)}.", ephemeral=True
            )
            return

        filename = backups[number - 1]
        await interaction.response.defer()
        ok, message = restore_backup(version.value, filename)
        await interaction.followup.send(message)