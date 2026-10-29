import os
import traceback

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from commands.foundry import FoundryGroup
from core.monitor import Monitor

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")


class SheepBot(commands.Bot):
    async def setup_hook(self):
        # Runs once per process (on_ready can fire again after reconnects).
        self.tree.add_command(FoundryGroup())
        self.tree.on_error = on_app_command_error
        await self.add_cog(Monitor(self))
        await self.tree.sync()


async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Reply with the actual error instead of Discord's generic 'interaction failed'."""
    original = getattr(error, "original", error)
    traceback.print_exception(original)
    msg = f"Something went wrong: `{type(original).__name__}: {original}`"[:1900]
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


intents = discord.Intents.default()
bot = SheepBot(command_prefix=commands.when_mentioned, intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}", flush=True)


bot.run(TOKEN)
