import discord
import os
from discord.ext import commands
from dotenv import load_dotenv

from commands.foundry import FoundryGroup

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)


@bot.event
async def on_ready():
    bot.tree.add_command(FoundryGroup())
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")


bot.run(TOKEN)