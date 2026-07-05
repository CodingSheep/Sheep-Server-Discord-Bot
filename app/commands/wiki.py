import discord
from discord import app_commands

from core.auth import is_allowed
from core.docker_ctl import build_wiki


class WikiGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="wiki", description="Manage the player wiki")

    async def _check_auth(self, interaction: discord.Interaction) -> bool:
        if not is_allowed(interaction.user.id):
            await interaction.response.send_message(
                "You're not authorized to use this command.", ephemeral=True
            )
            return False
        return True

    @app_commands.command(name="publish", description="Rebuild and publish the player wiki")
    async def publish(self, interaction: discord.Interaction):
        if not await self._check_auth(interaction):
            return
        await interaction.response.defer()
        ok, message = build_wiki()
        await interaction.followup.send(message)