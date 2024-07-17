import json
import re

from pathlib import Path

import discord

from dateutil import parser
from discord import app_commands
from discord import ui

from brunnerbot.utils import (
    is_team_admin,
    create_channel,
    move_channel,
    delete_channel,
    get_archive_category,
    get_ctf_archive_category,
    get_ctfs_category,
    get_complete_category,
    get_incomplete_category,
    get_export_channel,
    get_team_role,
    get_settings,
    sanitize_channel_name,
    MAX_CHANNELS
)
from brunnerbot.modules.ctftime import Ctftime
from brunnerbot.config import config

from brunnerbot.models.challenge import Challenge
from brunnerbot.models.ctf import Ctf


async def get_ctf_db(
    interaction: discord.Interaction,
    archived: bool | None = False,
    allow_chall: bool = True
) -> Ctf:
    ctf_db: Ctf = Ctf.objects(channel_id=interaction.channel_id).first()
    if ctf_db is None:
        chall_db: Challenge = Challenge.objects(channel_id=interaction.channel_id).first()
        if not allow_chall or chall_db is None:
            raise app_commands.AppCommandError("Not a CTF channel!")
        ctf_db: Ctf = chall_db.ctf
    if archived is False and ctf_db.archived:
        raise app_commands.AppCommandError("This CTF is archived!")
    if archived is True and not ctf_db.archived:
        raise app_commands.AppCommandError("This CTF is not archived!")
    return ctf_db


def user_to_dict(user: discord.Member | discord.User):
    return {
        "id": user.id,
        "nick": user.nick if isinstance(user, discord.Member) else None,
        "user": user.name,
        "avatar": user.avatar.key if user.avatar else None,
        "bot": user.bot,
    }


async def export_channels(channels: list[discord.TextChannel]):
    ctf_export = {"channels": []}
    for channel in channels:
        chan = {
            "name": channel.name,
            "topic": channel.topic,
            "messages": [],
            "pins": [m.id for m in await channel.pins()],
        }

        async for message in channel.history(limit=None, oldest_first=True):
            entry = {
                "id": message.id,
                "created_at": message.created_at.isoformat(),
                "content": message.clean_content,
                "author": user_to_dict(message.author),
                "attachments": [
                    {"filename": a.filename, "url": str(a.url)}
                    for a in message.attachments
                ],
                "channel": {
                    "name": message.channel.name
                },
                "edited_at": (
                    message.edited_at.isoformat()
                    if message.edited_at is not None
                    else message.edited_at
                ),
                "embeds": [e.to_dict() for e in message.embeds],
                "mentions": [user_to_dict(mention) for mention in message.mentions],
                "channel_mentions": [
                    {"id": c.id, "name": c.name}
                    for c in message.channel_mentions
                ],
                "mention_everyone": message.mention_everyone,
                "reactions": [
                    {
                        "count": r.count,
                        "emoji": r.emoji if isinstance(r.emoji, str) else {
                            "name": r.emoji.name,
                            "url": r.emoji.url
                        },
                    } for r in message.reactions
                ]
            }
            chan["messages"].append(entry)
        ctf_export["channels"].append(chan)
    return ctf_export


def create_info_message(info):
    msg = f"## {discord.utils.escape_mentions(info['title'])}"

    if "start" in info or "end" in info:
        msg += "\n"
    if "start" in info:
        msg += f"\n**START:** <t:{info['start']}:R> <t:{info['start']}>"
    if "end" in info:
        msg += f"\n**END:** <t:{info['end']}:R> <t:{info['end']}>"

    if "url" in info or "discord" in info:
        msg += "\n"
    if "url" in info:
        msg += f"\nCTF Link: {info['url']}"
    if "discord" in info:
        msg += f"\nDiscord: {info['discord']}"

    if "creds" in info:
        msg += f"\n\n### CREDENTIALS\n\n{discord.utils.escape_mentions(info['creds'])}"

    return msg


class CtfCommands(app_commands.Group):
    @app_commands.command(description="Create a new CTF event")
    @app_commands.guild_only
    @app_commands.check(is_team_admin)
    async def create(
        self,
        interaction: discord.Interaction,
        name: str,
        ctftime: str | None,
        private: bool = False
    ):
        if len(interaction.guild.channels) >= MAX_CHANNELS - 3:
            raise app_commands.AppCommandError("There are too many channels on this discord server")
        name = sanitize_channel_name(name)

        await interaction.response.defer(ephemeral=True)

        if existing_ctf := Ctf.objects(name=name).first():
            if interaction.guild.get_channel(existing_ctf.channel_id):
                await interaction.edit_original_response(
                    content="A CTF with that name already exists"
                )
                return

            # If found in DB but channel no longer exists, it's been deleted through Discord
            # Remove all challenges that have no corresponding channel
            for chall in Challenge.objects(ctf=existing_ctf):
                if not interaction.guild.get_channel(chall.channel_id):
                    chall.delete()

            # Check if any channels remain
            if Challenge.objects(ctf=existing_ctf).first() is not None:
                await interaction.edit_original_response(
                    content="Challenges from a CTF with that name still exist!\n"
                    "Please inspect all remains and force delete before retrying:\n"
                    f"`/ctf delete security:{name} force:True`"
                )
                return

            # Else delete the CTF and corresponding role
            try:
                await interaction.guild.get_role(existing_ctf.role_id).delete(
                    reason="Deleted CTF channels"
                )
            except AttributeError:
                pass
            existing_ctf.delete()

        settings = get_settings(interaction.guild)

        new_role = await interaction.guild.create_role(name=name + "-team")
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            new_role: discord.PermissionOverwrite(view_channel=True)
        }
        if not private and settings.use_team_role_as_acl:
            team_role = get_team_role(interaction.guild)
            overwrites[team_role] = discord.PermissionOverwrite(view_channel=True)
        if private:
            await interaction.user.add_roles(new_role)

        ctf_category = get_ctfs_category(interaction.guild)
        new_channel = await create_channel(name, overwrites, ctf_category, challenge=False)

        info = {"title": name}
        if ctftime:
            regex_ctftime = re.search(r"^(?:https?://ctftime.org/event/)?(\d+)/?$", ctftime)
            if regex_ctftime:
                info["ctftime_id"] = int(regex_ctftime.group(1))
                ctf_info = await Ctftime.get_ctf_info(info["ctftime_id"])
                info |= ctf_info

        info_msg = await new_channel.send(create_info_message(info))

        await info_msg.pin()

        ctf_db = Ctf(
            name=name,
            channel_id=new_channel.id,
            role_id=new_role.id,
            info=info,
            info_id=info_msg.id,
            private=private
        )
        ctf_db.save()

        await interaction.delete_original_response()
        await interaction.channel.send(f"Created CTF {new_channel.mention}")

        if not private and not settings.use_team_role_as_acl:
            for member in get_team_role(interaction.guild).members:
                await member.add_roles(new_role)


    @app_commands.command(description="Update CTF information")
    @app_commands.choices(field=[
        app_commands.Choice(name="title", value="title"),
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="end", value="end"),
        app_commands.Choice(name="url", value="url"),
        app_commands.Choice(name="discord", value="discord"),
        app_commands.Choice(name="creds", value="creds"),
        app_commands.Choice(name="ctftime", value="ctftime")
    ])
    @app_commands.guild_only
    @app_commands.check(is_team_admin)
    async def update(self, interaction: discord.Interaction, field: str, value: str):
        ctf_db = await get_ctf_db(interaction, archived=None)
        assert isinstance(interaction.channel, discord.TextChannel)

        info = ctf_db.info or {}
        if field == "title":
            info[field] = value.replace("\n", "")
        elif field in ("start", "end"):
            if value.isdigit():
                t = int(value)
            else:
                try:
                    t = int(parser.parse(value).timestamp())
                except parser.ParserError as exc:
                    raise app_commands.AppCommandError(
                        "Invalid time, please use any standard time format"
                    ) from exc

            info[field] = t
        elif field == "creds":
            c = value.split(":", 1)
            username, password = c[0], c[1] if len(c) > 1 else "password"
            original = f"Name: `{username}`\nPassword: `{password}`"

            class CredsModal(ui.Modal, title="Edit Credentials"):
                edit = ui.TextInput(
                    label="Edit",
                    style=discord.TextStyle.paragraph,
                    default=original,
                    max_length=1000
                )

                async def on_submit(self, submit_interaction: discord.Interaction):
                    info["creds"] = self.edit.value
                    ctf_db.info = info
                    ctf_db.save()
                    await interaction.channel.get_partial_message(ctf_db.info_id).edit(
                        content=create_info_message(info)
                    )
                    await submit_interaction.response.send_message("Updated info", ephemeral=True)

                    # Send and pin message just with password for easy copy paste
                    regex_password = re.search(r"Password: `(.+)`", self.edit.value)
                    if not regex_password:
                        return

                    password = regex_password.group(1)
                    if ctf_db.password_id is None:
                        msg = await interaction.channel.send(password)
                        await msg.pin()
                        ctf_db.password_id = msg.id
                        ctf_db.save()
                    else:
                        await interaction.channel.get_partial_message(ctf_db.password_id).edit(
                            content=password
                        )

            await interaction.response.send_modal(CredsModal())
            return
        elif field == "url":
            if not re.search(r"^https?://", value):
                raise app_commands.AppCommandError("Invalid URL")
            info["url"] = value
        elif field == "discord":
            regex_discord = re.search(
                r"^(?:https?://)?discord\.\w{2,3}/(?:invite/)?(\w+)/?$",
                value
            )
            if not regex_discord:
                raise app_commands.AppCommandError("Invalid Discord URL")

            info["discord"] = f"https://discord.gg/{regex_discord.group(1)}"
        elif field == "ctftime":
            regex_ctftime = re.search(r"^(?:https?://ctftime.org/event/)?(\d+)/?$", value)
            if not regex_ctftime:
                raise app_commands.AppCommandError("Invalid CTFtime link")

            info["ctftime_id"] = int(regex_ctftime.group(1))
            ctf_info = await Ctftime.get_ctf_info(info["ctftime_id"])
            for key, val in ctf_info.items():
                info[key] = val
        else:
            raise app_commands.AppCommandError("Invalid field")

        ctf_db.info = info
        ctf_db.save()
        await interaction.channel.get_partial_message(ctf_db.info_id).edit(
            content=create_info_message(info)
        )
        await interaction.response.send_message("Updated info", ephemeral=True)

    @app_commands.command(description="Archive a CTF")
    @app_commands.guild_only
    @app_commands.check(is_team_admin)
    async def archive(self, interaction: discord.Interaction):
        ctf_db = await get_ctf_db(interaction, allow_chall=False)
        assert isinstance(interaction.channel, discord.TextChannel)

        await interaction.response.defer()

        for chall in Challenge.objects(ctf=ctf_db):
            channel = interaction.guild.get_channel(chall.channel_id)
            if channel:
                await move_channel(channel, get_archive_category(interaction.guild))
            else:
                chall.delete()

        await move_channel(
            interaction.channel,
            get_ctf_archive_category(interaction.guild),
            challenge=False
        )
        ctf_db.archived = True
        ctf_db.save()
        await interaction.edit_original_response(content="The CTF has been archived")

    @app_commands.command(description="Unarchive a CTF")
    @app_commands.guild_only
    @app_commands.check(is_team_admin)
    async def unarchive(self, interaction: discord.Interaction):
        ctf_db = await get_ctf_db(interaction, archived=True, allow_chall=False)
        assert isinstance(interaction.channel, discord.TextChannel)

        await interaction.response.defer()

        for chall in Challenge.objects(ctf=ctf_db):
            channel = interaction.guild.get_channel(chall.channel_id)
            if chall.solved:
                target_category = get_complete_category(interaction.guild)
            else:
                target_category = get_incomplete_category(interaction.guild)
            if channel:
                await move_channel(channel, target_category)
            else:
                chall.delete()

        await move_channel(
            interaction.channel,
            get_ctfs_category(interaction.guild),
            challenge=False
        )
        ctf_db.archived = False
        ctf_db.save()
        await interaction.edit_original_response(content="The CTF has been unarchived")

    @app_commands.command(description="Rename a CTF and its channels")
    @app_commands.guild_only
    @app_commands.check(is_team_admin)
    async def rename(self, interaction: discord.Interaction, name: str):
        ctf_db = await get_ctf_db(interaction, allow_chall=False)
        assert isinstance(interaction.channel, discord.TextChannel)

        await interaction.response.defer()

        name = sanitize_channel_name(name)

        if ctf_db.info.get("title") == ctf_db.name:
            ctf_db.info["title"] = name
        ctf_db.name = name
        ctf_db.save()

        await interaction.channel.edit(name=name)

        for chall in Challenge.objects(ctf=ctf_db):
            channel = interaction.guild.get_channel(chall.channel_id)
            if channel:
                if chall.category:
                    await channel.edit(name=f"{name}-{chall.category}-{chall.name}")
                else:
                    await channel.edit(name=f"{name}-{chall.name}")
            else:
                chall.delete()
        await interaction.edit_original_response(content="The CTF has been renamed")

    @app_commands.command(description="Export a CTF")
    @app_commands.guild_only
    @app_commands.check(is_team_admin)
    async def export(self, interaction: discord.Interaction):
        ctf_db = await get_ctf_db(interaction, archived=None, allow_chall=False)
        assert isinstance(interaction.channel, discord.TextChannel)

        await interaction.response.defer()

        channels = [interaction.channel]

        for chall in Challenge.objects(ctf=ctf_db):
            channel = interaction.guild.get_channel(chall.channel_id)
            if channel:
                channels.append(channel)
            else:
                chall.delete()

        ctf_export = await export_channels(channels)

        export_dir = Path(config.backups_dir) / str(interaction.guild_id)
        export_dir.mkdir(exist_ok=True)

        filepath = export_dir / f"{interaction.channel_id}_{ctf_db.name}.json"
        try:
            with open(filepath, "w", encoding="utf8") as f:
                f.write(json.dumps(ctf_export, separators=(",", ":")))
        except FileNotFoundError:
            # Export dir was not created
            await interaction.edit_original_response(
                content="Invalid file permissions when exporting CTF"
            )
            return

        export_channel = get_export_channel(interaction.guild)
        await export_channel.send(files=[discord.File(filepath, filename=f"{ctf_db.name}.json")])
        await interaction.edit_original_response(content="The CTF has been exported")

    @app_commands.command(description="Delete a CTF and its channels")
    @app_commands.guild_only
    @app_commands.check(is_team_admin)
    async def delete(
        self,
        interaction: discord.Interaction,
        security: str | None,
        force: bool | None = False
    ):
        assert isinstance(interaction.channel, discord.TextChannel)

        if security is None:
            raise app_commands.AppCommandError(
                f"Please supply the security parameter \"{interaction.channel.name}\""
            )

        if force:
            ctf_db = Ctf.objects(name=security).first()
            if ctf_db is None:
                raise app_commands.AppCommandError(f"No CTF in DB with name {security}")
        else:
            ctf_db = await get_ctf_db(interaction, archived=None, allow_chall=False)
            if security != interaction.channel.name:
                raise app_commands.AppCommandError("Wrong security parameter")

        await interaction.response.defer()

        for chall in Challenge.objects(ctf=ctf_db):
            try:
                await delete_channel(interaction.guild.get_channel(chall.channel_id))
            except AttributeError:
                pass

        try:
            await interaction.guild.get_role(ctf_db.role_id).delete(reason="Deleted CTF channels")
        except AttributeError:
            pass

        ctf_channel = interaction.guild.get_channel(ctf_db.channel_id)
        try:
            await delete_channel(ctf_channel)
        except AttributeError:
            pass

        Challenge.objects(ctf=ctf_db).delete()
        ctf_db.delete()

        if interaction.channel != ctf_channel:
            await interaction.edit_original_response(
                content="CTF deleted successfully"
            )


@app_commands.command(description="Invite a user to the CTF")
@app_commands.guild_only
async def invite(interaction: discord.Interaction, user: discord.Member):
    ctf_db = await get_ctf_db(interaction)
    assert isinstance(interaction.channel, discord.TextChannel)

    await user.add_roles(
        interaction.guild.get_role(ctf_db.role_id),
        reason=f"Invited by {interaction.user.name}"
    )
    await interaction.response.send_message(f"Invited user {user.mention}")


@app_commands.command(description="Leave a CTF")
@app_commands.guild_only
async def leave(interaction: discord.Interaction):
    ctf_db = await get_ctf_db(interaction)
    assert isinstance(interaction.channel, discord.TextChannel)

    ctf_role = interaction.guild.get_role(ctf_db.role_id)
    if ctf_role in interaction.user.roles:
        await interaction.user.remove_roles(ctf_role, reason="Left CTF")
        await interaction.response.send_message(f"{interaction.user.mention} Left the CTF")
    else:
        await interaction.response.send_message("Cannot leave CTF", ephemeral=True)


@app_commands.command(description="Remove a user from the CTF")
@app_commands.guild_only
@app_commands.check(is_team_admin)
async def remove(interaction: discord.Interaction, user: discord.Member):
    ctf_db = await get_ctf_db(interaction)
    assert isinstance(interaction.channel, discord.TextChannel)

    ctf_role = interaction.guild.get_role(ctf_db.role_id)
    if ctf_role in user.roles:
        await user.remove_roles(ctf_role, reason=f"Removed by {interaction.user.name}")
        await interaction.response.send_message(f"Removed user {user.mention}")
    else:
        await interaction.response.send_message("Cannot remove user from CTF", ephemeral=True)


def add_commands(tree: app_commands.CommandTree, guild: discord.Object | None):
    tree.add_command(CtfCommands(name="ctf"), guild=guild)
    tree.add_command(invite, guild=guild)
    tree.add_command(leave, guild=guild)
    tree.add_command(remove, guild=guild)
