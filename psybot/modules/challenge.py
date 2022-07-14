import re
from typing import Optional, Tuple

from discord import app_commands
import discord
from pymongo.errors import DuplicateKeyError

from psybot.config import config
from psybot.models.ctf_category import CtfCategory
from psybot.utils import move_channel
from psybot.modules.ctf import category_autocomplete

from psybot.models.challenge import Challenge
from psybot.models.ctf import Ctf


async def check_challenge(interaction: discord.Interaction) -> Tuple[Optional[Challenge], Optional[Ctf]]:
    chall_db: Challenge = Challenge.objects(channel_id=interaction.channel.id).first()
    if chall_db is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Not a challenge!", ephemeral=True)
        return None, None
    ctf_db: Ctf = chall_db.ctf
    if ctf_db.archived:
        await interaction.response.send_message("This CTF is archived!", ephemeral=True)
        return None, None
    return chall_db, ctf_db


@app_commands.command(description="Marks a challenge as done")
async def done(interaction: discord.Interaction, contributors: Optional[str]):
    chall_db, ctf_db = await check_challenge(interaction)
    if chall_db is None or not isinstance(interaction.channel, discord.TextChannel):
        return

    users = chall_db.solvers
    if interaction.user.id not in users:
        users.append(interaction.user.id)
    if contributors is not None:
        for user in [int(i) for i in re.findall(r'<@!?(\d+)>', contributors)]:
            if user not in users:
                users.append(user)

    chall_db.solvers = users
    chall_db.solved = True
    chall_db.save()

    await move_channel(interaction.channel, interaction.guild.get_channel(config.complete_category))

    msg = ":tada: {} was solved by ".format(interaction.channel.mention) + " ".join(f"<@!{user}>" for user in users) + " !"
    await interaction.guild.get_channel(ctf_db.channel_id).send(msg)
    await interaction.response.send_message("Challenge moved to done!")


@app_commands.command(description="Marks a challenge as undone")
async def undone(interaction: discord.Interaction):
    chall_db, ctf_db = await check_challenge(interaction)
    if chall_db is None or not isinstance(interaction.channel, discord.TextChannel):
        return

    if not chall_db.solved:
        await interaction.response.send_message("This challenge is not done yet!", ephemeral=True)
        return

    chall_db.solvers = []
    chall_db.solved = False
    chall_db.save()

    await move_channel(interaction.channel, interaction.guild.get_channel(config.incomplete_category))
    await interaction.response.send_message("Reopened challenge as not done")


class CategoryCommands(app_commands.Group):

    @app_commands.command(description="Create CTF category suggestion")
    async def create(self, interaction: discord.Interaction, category: str):
        try:
            ctf_category = CtfCategory(name=category, count=5)
            ctf_category.save()
        except DuplicateKeyError:
            await interaction.response.send_message("CTF category already exists", ephemeral=True)
        else:
            await interaction.response.send_message("Created CTF category", ephemeral=True)

    @app_commands.command(description="Delete CTF category suggestion")
    @app_commands.autocomplete(category=category_autocomplete)
    async def delete(self, interaction: discord.Interaction, category: str):
        if not interaction.guild.get_role(config.admin_role) in interaction.user.roles:
            await interaction.response.send_message("Only an admin can delete categories", ephemeral=True)
            return
        ctf_category: CtfCategory = CtfCategory.objects(name=category).first()
        if ctf_category is None:
            await interaction.response.send_message("Unknown CTF category", ephemeral=True)
        else:
            ctf_category.delete()
            await interaction.response.send_message("Deleted CTF category", ephemeral=True)


def add_commands(tree: app_commands.CommandTree):
    tree.add_command(done, guild=discord.Object(id=config.guild_id))
    tree.add_command(undone, guild=discord.Object(id=config.guild_id))
    tree.add_command(CategoryCommands(name="category"), guild=discord.Object(id=config.guild_id))
