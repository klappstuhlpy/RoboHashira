from __future__ import annotations
from typing import Optional

from discord.ext import commands
import discord
from discord import app_commands

from .config import GuildConfig
from .utils.context import Context
from .utils import formats, _commands
from bot import RoboHashira


def preview_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title='Music Player Panel',
        description=f'The **control panel** was closed, the Queue is currently empty and I got nothing to do.\n'
                    f'You can start a new player session by invoking the </play:1079059790380142762> command.\n\n'
                    f'*Once you play a new track, this message is going to be the new player panel if it\'s not deleted, otherwise I\'m going to create a new panel.*',
        timestamp=discord.utils.utcnow(),
        color=formats.Colour.teal())
    embed.set_footer(text='last updated')
    embed.set_thumbnail(url=guild.icon.url if not None else None)
    return embed


class ChooseView(discord.ui.View):
    def __init__(self, cog: Setup):
        super().__init__()
        self.cog: Setup = cog
        self.bot: RoboHashira = cog.bot

    @discord.ui.button(label='Create a Text Channel', custom_id='create-channel', emoji='➕',
                       style=discord.ButtonStyle.green)
    async def create_channel(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        await interaction.message.delete()
        channel = await interaction.guild.create_text_channel(name='🎶hashira-music',
                                                              category=interaction.channel.category)
        await channel.edit(slowmode_delay=3,
                           topic=f'This is the Channel where you can see {self.bot.user.mention}\'s current playing songs.\n'
                                 f'You can interact with the **control panel** and manage the current songs.\n'
                                 f'\n'
                                 f'__Be careful not to delete the **control panel** message.__\n'
                                 f'If you accidentally deleted the message, you have to redo the setup with </setup:1066792241978425528>.\n'
                                 f'\n'
                                 f'ℹ️** | Every Message if not pinned, gets deleted within 60 seconds.**')

        await interaction.response.send_message(
            f'<:greenTick:1079249732364406854> Successfully set the new player channel to {channel.mention}.')

        message = await channel.send(embed=preview_embed(interaction.guild))
        await message.pin()
        await channel.purge(limit=5, check=lambda msg: not msg.pinned)

        config: GuildConfig = await self.bot.cfg.get_config(interaction.guild.id)
        await config.edit(music_channel=channel.id, music_message_id=message.id)

    @discord.ui.button(label='Cancel', custom_id='cancel', style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.delete()


class Setup(commands.Cog):
    """Additional Music Tools for the Music Cog.
    Like: Playlist, DJ, Setup etc."""

    def __init__(self, bot):
        self.bot: RoboHashira = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='staff_animated', id=1076911514193231974)

    @_commands.command(
        commands.hybrid_group,
        name='dj',
        description='Manage the DJ Role.',
        guild_only=True
    )
    @_commands.permissions(user=['manage_roles'])
    async def _dj(self, ctx: Context):
        """Manage the DJ Role.
        The bot and you both need to have the **Manage Roles** permission to use this command.
        """
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_commands.command(
        _dj.command,
        name='add',
        description='Adds the DJ Role with which you have extended control rights to a member.'
    )
    @app_commands.describe(member='The member you want to add the DJ Role to.')
    async def dj_add(self, ctx: Context, member: discord.Member):
        """Adds the DJ Role with which you have extended control rights to a member."""
        djRole = discord.utils.get(ctx.guild.roles, name='DJ')
        if djRole is None:
            try:
                djRole = await ctx.guild.create_role(name='DJ')

                await member.add_roles(djRole)
                return await ctx.send(
                    f'<:greenTick:1079249732364406854> Added and created the {djRole.mention} role to user {member}.',
                    ephemeral=True)
            except commands.BotMissingPermissions:
                return await ctx.send(
                    embed=discord.Embed(title='Missing Required Permissions',
                                        description=f'<:redTick:1079249771975413910> An error occurred while executing ``/dj add``.\n'
                                                    f'There is currently no ``DJ`` role.'
                                                    f'In order to create one and manage roles,\ni need to have the ``MANAGE_ROLES`` permission.',
                                        color=discord.Color.red()).set_footer(
                        text=f'Requested by: {ctx.author}', icon_url=ctx.author.avatar.url), ephemeral=True,
                    delete_after=10)

        if djRole in member.roles:
            return await ctx.stick(False, f'{member} already has the DJ role.', ephemeral=True)
        await member.add_roles(djRole)
        await ctx.stick(True, f'Added the {djRole.mention} role to user {member}.', ephemeral=True)

    @_commands.command(
        _dj.command,
        name='remove',
        description='Removes the DJ Role with which you have extended control rights from a member.'
    )
    @app_commands.describe(member='The member you want to remove the DJ Role from.')
    async def dj_remove(self, ctx: Context, member: discord.Member):
        """Removes the DJ Role with which you have extended control rights from a member."""
        djRole = discord.utils.get(ctx.guild.roles, name='DJ')
        if djRole:
            try:
                if djRole not in member.roles:
                    return await ctx.stick(False, f'{member} has not the DJ role.',
                                           ephemeral=True)

                await member.remove_roles(djRole)
                return await ctx.send(
                    f'<:greenTick:1079249732364406854> Removed the {djRole.mention} role from user {member.mention}.',
                    ephemeral=True)
            except commands.BotMissingPermissions:
                return await ctx.send(
                    embed=discord.Embed(title='RHashira Missing Required Permissions',
                                        description=f'An error occurred while executing ``/dj remove``.\n'
                                                    f'In order manage the roles,\ni need to have the ``MANAGE_ROLES`` permission.',
                                        color=discord.Color.red()).set_footer(
                        text=f'Requested by: {ctx.author}', icon_url=ctx.author.avatar.url), ephemeral=True,
                    delete_after=10)
        else:
            return await ctx.stick(False, 'There is currently no existing DJ role.',
                                   ephemeral=True, delete_after=10)

    # SETUP

    @_commands.command(
        commands.hybrid_group,
        name='setup',
        description='Start the Music configuration setup.',
        fallback='set',
        guild_only=True
    )
    @_commands.permissions(user=['manage_channels'])
    async def setup(self, ctx: Context, channel: Optional[discord.TextChannel] = None):
        """Start the Music configuration setup.
        To use this command the bot and you both need the **Manage Channels** permission.
        You can create a new text channel by typing the Command without any arguments, or you can
        choose an existing text channel by typing the Command with the text channel as an argument."""

        if ctx.interaction:
            await ctx.defer()

        if not channel:
            embed = discord.Embed(
                title='In which text channel should the **plugins control panel** be displayed?\n',
                description=f'**If you want to add an existing channel, use `{ctx.prefix}setup <#channel>`.**')
            embed.set_thumbnail(url=ctx.guild.icon.url)
            await ctx.send(embed=embed, view=ChooseView(self))
        else:
            await channel.edit(slowmode_delay=3,
                               topic=f'This is the Channel where you can see {self.bot.user.mention}\'s current playing songs.\n'
                                     f'You can interact with the **control panel** and manage the current songs.\n'
                                     f'\n'
                                     f'__Be careful not to delete the **control panel** message.__\n'
                                     f'If you accidentally deleted the message, you have to redo the setup with </setup:1066792241978425528>.\n'
                                     f'\n'
                                     f'ℹ️** | Every Message if not pinned, gets deleted within 60 seconds.**')

            await ctx.send(
                f'<:greenTick:1079249732364406854> Successfully set the new player channel to {channel.mention}.')

            message = await channel.send(embed=preview_embed(ctx.guild))
            await message.pin()
            await channel.purge(limit=5, check=lambda msg: not msg.pinned)

            config: GuildConfig = await self.bot.cfg.get_config(ctx.guild.id)
            await config.edit(music_channel=channel.id, music_message_id=message.id)

    @_commands.command(
        setup.command,
        name='reset',
        description='Reset the Music configuration setup.'
    )
    @commands.guild_only()
    async def setup_reset(self, ctx: Context):
        """Reset the Music configuration setup."""
        config: GuildConfig = await self.bot.cfg.get_config(ctx.guild.id)
        if not config or (not config.music_channel or not config.music_message_id):
            return await ctx.stick(False, 'There is currently no Music Configuration.', ephemeral=True, delete_after=10)

        await config.edit(music_channel=None, music_message_id=None)
        await ctx.send('<:greenTick:1079249732364406854> The Music Configuration for this Guild has been deleted.',
                       ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.bot.wait_until_ready()
        if message.guild is None:
            return
        config: GuildConfig = await self.bot.cfg.get_config(message.guild.id)
        if not config or (not config.music_channel or not config.music_message_id):
            return

        if message.channel.id == config.music_channel:
            if not message.pinned:
                if message.id != config.music_message_id:
                    await message.delete(delay=60)


async def setup(bot):
    await bot.add_cog(Setup(bot))
