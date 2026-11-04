import asyncio

import discord
from discord import app_commands

from core import wiki_auth
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
        ok, message = await asyncio.to_thread(build_wiki)
        await interaction.followup.send(message)

    @app_commands.command(name="password", description="Set the wiki's shared player password (only you see the reply)")
    @app_commands.describe(password="New password (12+ characters). Leave empty to generate one.")
    async def password(self, interaction: discord.Interaction, password: str | None = None):
        if not await self._check_auth(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        new = password or wiki_auth.generate()
        ok, message = await asyncio.to_thread(wiki_auth.set_password, new)
        if not ok:
            await interaction.followup.send(f"❌ {message}", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ Wiki login changed. It works right away; players get a login prompt on their next visit.\n"
            f"Username: `{wiki_auth.WIKI_USER}`\nPassword: ||`{new}`||",
            ephemeral=True)
