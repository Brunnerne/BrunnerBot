import asyncio
import logging
import sys

import discord
import pymongo.errors
from discord import app_commands

from brunnerbot.modules import ctf, ctftime, challenge, notes, bot
from brunnerbot.config import config
from brunnerbot.database import db
from brunnerbot.utils import setup_settings

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.all()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

GUILD_OBJ = discord.Object(id=config.guild_id) if config.guild_id else None
challenge.add_commands(tree, GUILD_OBJ)
ctf.add_commands(tree, GUILD_OBJ)
ctftime.add_commands(tree, GUILD_OBJ)
notes.add_commands(tree, GUILD_OBJ)
bot.add_commands(tree, GUILD_OBJ)


@client.event
async def setup_hook():
    client.add_view(notes.ModalNoteView())
    client.add_view(notes.HedgeDocNoteView(""))
    client.add_view(challenge.WorkView())


@client.event
async def on_ready():
    try:
        db.command("ping")
    except pymongo.errors.ServerSelectionTimeoutError:
        logging.critical("Could not connect to MongoDB")
        sys.exit(1)

    if config.guild_id:
        guild = client.get_guild(config.guild_id)
        if guild:
            await setup_settings(guild)
            await tree.sync(guild=GUILD_OBJ)
    else:
        for guild in client.guilds:
            await setup_settings(guild)
        await tree.sync(guild=GUILD_OBJ)
    logging.info("%s is online", client.user.name)


@client.event
async def on_guild_join(guild: discord.Guild):
    if config.guild_id is None or config.guild_id == guild.id:
        logging.info("%s has joined guild \"%s\"", client.user.name, guild.name)
        await setup_settings(guild)
        if config.guild_id:
            await tree.sync(guild=GUILD_OBJ)


@tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    try:
        raise error
    except app_commands.CommandInvokeError as e:
        try:
            raise e.original
        except AssertionError:
            await interaction.response.send_message(
                "An assertion failed when running this command",
                ephemeral=True
            )
    except app_commands.AppCommandError:
        if error.args:
            if interaction.response.is_done():
                await interaction.edit_original_response(content=error.args[0])
            else:
                await interaction.response.send_message(error.args[0], ephemeral=True)


async def main():
    async with client:
        await client.start(config.bot_token)


if __name__ == "__main__":
    asyncio.run(main())
