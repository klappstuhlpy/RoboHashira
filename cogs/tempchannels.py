from __future__ import annotations

from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands

from bot import RoboHashira
from .config import GuildConfig, TempChannel, ModifyType
from .utils._commands import PermissionTemplate
from .utils.context import Context
from .utils import checks, fuzzy, _commands
from .utils.formats import plural


class TempChannels(commands.Cog):
    """Create temporary voice hub channels for users to join."""

    def __init__(self, bot: RoboHashira):
        self.bot: RoboHashira = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='\N{HOURGLASS}')

    async def temp_channel_id_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        config: GuildConfig = await self.bot.cfg.get_config(interaction.guild_id)
        if not config.temp_channels:
            return []
        channels = [self.bot.get_channel(temp.id) for temp in config.temp_channels]
        results = fuzzy.finder(current, channels, key=lambda t: t.name)
        return [app_commands.Choice(value=str(result.id), name=result.name) for result in results][:25]

    @_commands.command(
        commands.hybrid_group,
        name='temp',
        description='Manage Temp Channels.',
        guild_only=True
    )
    @_commands.permissions(user=['manage_channels'])
    async def _temp(self, ctx: Context):
        """Get an overview of the Use of the TempChannels.
        To manage Temp Channels, (you and) the bot must have the following permissions:
        - **Manage Channels**
        - **Move Members**"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_commands.command(_temp.command, name='list', description='List of current temporary channels.')
    async def temp_list(self, ctx: Context):
        """List of current temporary channels."""
        config: GuildConfig = await self.bot.cfg.get_config(ctx.guild.id)
        if not config.temp_channels:
            await ctx.stick(False, 'There are no temporary channels set up.',
                                   ephemeral=True)
            return

        items = [temp.to_field(index) for index, temp in enumerate(config.temp_channels, 1)]
        embed = discord.Embed(title='Temporary Voice Hubs',
                              description='\n'.join(items),
                              color=self.bot.colour.teal())
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
        embed.set_footer(text=f'{plural(len(config.temp_channels)):channel}')
        await ctx.send(embed=embed)

    @_commands.command(_temp.command, name='add', description='Sets the channel where to create a temporary channel.')
    @app_commands.rename(_format='format')
    @app_commands.describe(
        channel='Enter a voice-channel.',
        _format='Set the temp channel format. (Default: ⏳ | %username : %username -> replaced with name of User.)')
    async def temp_add(self, ctx: Context, channel: discord.VoiceChannel, _format: Optional[str] = '⏳ | %username'):
        """Sets the channel where to create a temporary channel."""
        config: GuildConfig = await self.bot.cfg.get_config(ctx.guild.id)
        if config and discord.utils.get(config.temp_channels, id=channel.id):
            await ctx.stick(False, 'This is already a Temporary Voice Hub.',
                                   ephemeral=True)
            return

        await config.edit(temp_channels=[(TempChannel(channel.id, _format), ModifyType.ADD)])
        await ctx.send(
            f'<:greenTick:1079249732364406854> Successfully added {channel.mention} with format **`{_format}`**.')

    @_commands.command(_temp.command, name='edit', description='Edit the format of a active Temp Channel.')
    @app_commands.rename(_format='format')
    @app_commands.describe(
        channel_id='Enter a Voice Hub ID.',
        _format='Set the temp channel format. (Default: ⏳ | %username : %username -> replaced with name of User.)')
    @app_commands.autocomplete(channel_id=temp_channel_id_autocomplete)  # type: ignore
    async def temp_edit(self, ctx: Context, channel_id: str, _format: Optional[str] = '⏳ | %username'):
        """Edit the format of an active Temp Channel."""
        config: GuildConfig = await self.bot.cfg.get_config(ctx.guild.id)
        if not config.temp_channels:
            await ctx.stick(False, 'There are no temporary channels set up.',
                                   ephemeral=True)
            return

        if not channel_id.isdigit():
            await ctx.stick(False, 'This is not a Temporary Voice Hub.',
                                   ephemeral=True)
            return

        channel_id = int(channel_id)
        channel = discord.utils.get(config.temp_channels, id=channel_id)
        if not channel:
            await ctx.stick(False, 'This is not a Temporary Voice Hub.',
                                   ephemeral=True)
            return

        await config.edit(temp_channels=[(TempChannel(channel_id, _format), ModifyType.EDIT)])
        await ctx.send(
            f'<:greenTick:1079249732364406854> Successfully edited <#{channel.id}> with format **`{_format}`**.')

    @_commands.command(_temp.command, name='remove', description='Remove a existing temp channel.')
    @app_commands.describe(channel_id='Enter a Voice Hub ID.')
    @app_commands.autocomplete(channel_id=temp_channel_id_autocomplete)  # type: ignore
    async def temp_remove(self, ctx: Context, channel_id: str):
        """Remove an existing temp channel."""
        config: GuildConfig = await self.bot.cfg.get_config(ctx.guild.id)
        if not config.temp_channels:
            await ctx.stick(False, 'There are no temporary channels set up.',
                                   ephemeral=True)
            return

        if not channel_id.isdigit():
            await ctx.stick(False, 'This is not a Temporary Voice Hub.',
                                   ephemeral=True)
            return

        channel_id = int(channel_id)
        channel = discord.utils.get(config.temp_channels, id=channel_id)
        if not channel:
            await ctx.stick(False, 'This is not a Temporary Voice Hub.',
                                   ephemeral=True)
            return

        await config.edit(temp_channels=[(channel, ModifyType.REMOVE)])
        await ctx.send('<:greenTick:1079249732364406854> Successfully removed the Temporary Voice Hub.')

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        await self.bot.wait_until_ready()

        if before.channel:
            if self.bot.temp_channels.get(before.channel.id):
                if len(before.channel.members) == 0:
                    await self.bot.temp_channels.remove(before.channel.id)
                    await before.channel.delete()

        elif after.channel and before.channel is None:
            config: GuildConfig = await self.bot.cfg.get_config(member.guild.id)
            if not config.temp_channels:
                return

            if temp := discord.utils.get(config.temp_channels, id=after.channel.id):
                try:
                    channel = await member.guild.create_voice_channel(
                        name=f'{temp:{member.display_name}}',
                        category=after.channel.category,
                        reason=f'Temporary Voice Hub for {member.display_name} ({member.id})')
                    ow = discord.PermissionOverwrite(manage_channels=True, manage_roles=True, move_members=True)
                    await channel.set_permissions(member, overwrite=ow)

                    await member.move_to(channel)
                    await self.bot.temp_channels.put(channel.id, True)
                except discord.HTTPException as exc:
                    if exc.code == 50013:
                        await member.guild.system_channel.send(
                            f'<:warning:1076913452775383080> {member.mention} I don\'t have the permissions to create or '
                            f'manage a temporary channel in **{after.channel.category}**.')
                    else:
                        await member.guild.system_channel.send(
                            f'<:warning:1076913452775383080> {member.mention} An error occurred while creating a temporary channel.')


async def setup(bot: RoboHashira):
    await bot.add_cog(TempChannels(bot))
