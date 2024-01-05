from __future__ import annotations
import inspect
import itertools
import time
from typing import Optional, Mapping, Union, List, Annotated, Type, Dict, TYPE_CHECKING, Iterable, Callable

import aiohttp
import discord
from discord import app_commands
from jishaku.math import mean_stddev

from .utils import fuzzy, formats, _commands
from discord.ext import commands

from .utils._commands import PermissionTemplate
from .utils.context import Context, GuildContext
from .utils.converters import Prefix
from cogs.utils.paginator import BasePaginator
from .utils.formats import plural

if TYPE_CHECKING:
    from bot import RoboHashira


PartialCommandGroup = Union[commands.Group | commands.hybrid.HybridGroup | app_commands.commands.Group]
PartialCommand = Union[commands.Command | app_commands.commands.Command | commands.hybrid.HybridCommand]

RH_MUSIC_GUILD_ID = 1066703165669515264
COMMAND_ICON_URL = 'https://cdn.discordapp.com/emojis/782701715479724063.webp?size=96&quality=lossless'


class GroupHelpPaginator(BasePaginator):
    group: Union[commands.Group, commands.Cog]
    prefix: str
    groups: Dict[commands.Cog, list[commands.Command], list[app_commands.AppCommand]]

    async def format_page(self, entries: List[commands.Command]):
        emoji = getattr(self.group, 'display_emoji', None) or ''
        embed = discord.Embed(title=f'{emoji} {self.group.qualified_name} Commands',
                              description=self.group.description,
                              colour=formats.Colour.teal())

        is_app_command_cog = False
        if isinstance(self.group, commands.Cog):
            if not list(filter(lambda c: not c.hidden, self.group.get_commands())):
                is_app_command_cog = True

        for command in entries:
            if isinstance(command, app_commands.commands.Command):
                embed.add_field(name=command.qualified_name, value=command.description or 'No help given...',
                                inline=False)
            else:
                signature = f'{command.qualified_name} {command.signature}{' *(hidden)*' if command.hidden else ""}'
                embed.add_field(name=signature, value=command.short_doc or 'No help given...', inline=False)

        embed.set_author(name=f'{plural(len(self.entries)):command}', icon_url=COMMAND_ICON_URL)
        if is_app_command_cog:
            embed.set_footer(text=f'Those Commands are only available in Slash Commands.')
        else:
            embed.set_footer(text=f'Use "{self.prefix}help command" for more info on a command.')

        return embed

    @classmethod
    async def start(
            cls: Type[GroupHelpPaginator],
            context: Context | discord.Interaction,
            *,
            entries: List,
            per_page: int = 6,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            edit: bool = False,
            prefix: str = None,
            group: Union[commands.Group, commands.Cog] = None,
            groups: Optional[Dict[commands.Cog, list[commands.Command], list[app_commands.AppCommand]]] = None,
    ) -> GroupHelpPaginator[commands.Command]:
        """Overwritten to add the view to the message and edit message, not send new."""
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        self.prefix = getattr(context, 'prefix', None) or prefix
        self.group = group or entries[0].cog
        self.groups = groups

        page: discord.Embed = await self.format_page(self.pages[0])  # type: ignore

        if self.groups is not None:
            self.add_item(HelpSelectMenu(self.groups, getattr(context, 'bot', context.client)))  # type: ignore
        self.update_buttons()

        if edit:
            await self._edit(context, embed=page, view=self)
        else:
            if self.total_pages <= 1:
                await self._send(context, embed=page, ephemeral=ephemeral)
            else:
                await self._send(context, embed=page, view=self, ephemeral=ephemeral)
        return self

    @classmethod
    async def _edit(cls, context, **kwargs) -> discord.Message:
        if isinstance(context, discord.Interaction):
            if context.response.is_done():
                msg = await context.edit_original_response(**kwargs)
            else:
                msg = await context.response.edit_message(**kwargs)
        else:
            msg = await context.message.edit(**kwargs)
        return msg


class HelpSelectMenu(discord.ui.Select):
    def __init__(self, entries: dict[commands.Cog, list[commands.Command]], bot: RoboHashira):
        super().__init__(
            placeholder='Select a category to view...',
            row=1,
        )
        self.commands: dict[commands.Cog, list[PartialCommand]] = entries
        self.bot: RoboHashira = bot
        self.__fill_options()

    def __fill_options(self) -> None:
        self.add_option(
            label='Start Page',
            emoji=discord.PartialEmoji(name='vegaleftarrow', id=1066024601332748389),
            value='__index',
            description='The front page of the Help Menu.',
        )

        for cog, commands in self.commands.items():
            if not commands:
                continue
            description = cog.description.split('\n', 1)[0] or None
            emoji = getattr(cog, 'display_emoji', None)
            self.add_option(label=cog.qualified_name, value=cog.qualified_name, description=description, emoji=emoji)

    async def callback(self, ctx: discord.Interaction):
        assert self.view is not None
        value = self.values[0]
        if value == '__index':
            await FrontHelpPaginator.start(ctx, entries=self.commands, edit=True)
        else:
            cog = self.bot.get_cog(value)
            if cog is None:
                await ctx.response.send_message('Somehow this category does not exist?', ephemeral=True)
                return

            commands = self.commands[cog]
            if not commands:
                await ctx.response.send_message('This category has no commands for you', ephemeral=True)
                return

            prefix = '/' if isinstance(self.view.ctx, discord.Interaction) else self.view.ctx.clean_prefix
            await GroupHelpPaginator.start(ctx, entries=commands, edit=True, group=cog, prefix=prefix,
                                           groups=self.view.groups)


class FrontHelpPaginator(BasePaginator):
    groups: dict[commands.Cog, list[commands.Command], list[app_commands.AppCommand]]

    async def format_page(self, entries: Dict, /):
        embed = discord.Embed(title=f'{self.ctx.client.user.name}\'s Help Page', colour=formats.Colour.teal())
        embed.set_thumbnail(url=self.ctx.client.user.avatar.url)
        pref = '/' if isinstance(self.ctx, discord.Interaction) else self.ctx.clean_prefix
        embed.description = inspect.cleandoc(
            f"""
            ## Introduction
            Here you can find all *Message-/Slash-Commands* for {self.ctx.client.user.name}.
            Try using the dropdown to navigate through the categories to get a list of all Commands.
            Alternatively, you can use the following Commands to get Information about a specific Command or Category:
            ## More Help
            - `{pref}help` *`command`*
            - `{pref}help` *`category`*
            """
        )

        pag_help = self.ctx.client.help_command.temporary(self.ctx)
        if self._current_page == 0:
            embed.description += '\n' + inspect.cleandoc(
                f"""
                ## Support
                For more help, consider joining the official server over at
                https://discord.com/invite/eKwMtGydqh.
                ## Stats
                Total of **{await pag_help.total_commands_invoked()}** command runs.
                Currently are **{len(pag_help.all_commands)}** commands loaded.
                """
            )
        elif self._current_page == 1:
            entries = [
                ('<argument>', 'This argument is **required**.'),
                ('[argument]', 'This argument is **optional**.'),
                ('[A|B]', 'This means **multiple choices**, you can choose by using one.'),
                ('[argument...]', 'There are multiple Arguments.'),
                (
                    '\u200b',
                    '<:discord_info:1113421814132117545> **Important:**\n'
                    'Do not type the arguments in brackets.\n'
                    'Most of the Commands are **Hybrid Commands**, which means that you can use them as Slash Commands or Message Commands.'
                ),
            ]
            for name, value in entries:
                embed.add_field(name=name, value=value, inline=False)

        embed.set_footer(text=f'I was created at')
        embed.timestamp = self.ctx.client.user.created_at

        return embed

    @classmethod
    async def start(
            cls: Type[FrontHelpPaginator],
            context: Context | discord.Interaction,
            *,
            entries: Dict,
            per_page: int = 1,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            edit: bool = False
    ) -> FrontHelpPaginator[str]:
        """Overwritten to add the SelectMenu"""
        self = cls(entries=['INDEX1', 'INDEX2'],  # just some placeholders, doing embed stuff in format_page directly
                   per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context
        self.groups = entries

        page = await self.format_page(self.pages[0])

        kwargs = {'view': self, 'embed' if isinstance(page, discord.Embed) else 'content': page}
        if self.total_pages <= 1:
            kwargs.pop('view')

        self.add_item(HelpSelectMenu(entries, self.ctx.client))  # type: ignore
        self.update_buttons()

        if edit:
            if isinstance(context, discord.Interaction):
                self.msg = await context.response.edit_message(**kwargs)
            else:
                self.msg = await context.message.edit(embed=page, view=self)
        else:
            self.msg = await cls._send(context, ephemeral, **kwargs)
        return self


class PaginatedHelpCommand(commands.HelpCommand):
    context: Context

    def __init__(self):
        super().__init__(
            show_hidden=False,
            verify_checks=False,
            command_attrs={
                'cooldown': commands.CooldownMapping.from_cooldown(1, 3.0, commands.BucketType.member),
                'hidden': True,
                'aliases': ['h'],
                'usage': '[command|category]',
                'description': 'Get help for a module or a command.'
            }
        )

    @property
    def all_commands(self) -> set[PartialCommand]:
        return set(self.context.client.commands) | set(self.context.client.tree._get_all_commands())

    @staticmethod
    def get_cog_commands(cog: commands.Cog) -> set[PartialCommand]:
        return set(cog.get_commands()) | set(cog.get_app_commands())

    async def total_commands_invoked(self) -> int:
        query = 'SELECT COUNT(*) as total FROM commands;'
        return await self.context.client.pool.fetchval(query)  # type: ignore

    async def command_callback(self, ctx: Context, /, *, command: Optional[str] = None):  # noqa
        """|coro|

        The actual implementation of the help command.

        Implemention of Mine:
        - Added Support for HybridCommands, AppCommands, AppCommand Groups/SubCommands
        """
        await self.prepare_help_command(ctx, command)

        if command is None:
            mapping = self.get_bot_mapping()
            return await self.send_bot_help(mapping)

        cog = ctx.bot.get_cog(command)
        if cog is not None:
            return await self.send_cog_help(cog)

        maybe_coro = discord.utils.maybe_coroutine

        keys = command.split(' ')
        cmd = discord.utils.find(lambda c: c.name == keys[0] or c.qualified_name == keys[0], self.all_commands)
        if cmd is None:
            string = await maybe_coro(self.command_not_found, self.remove_mentions(keys[0]))  # type: ignore
            return await self.send_error_message(string)

        for key in keys[1:]:
            try:
                if isinstance(cmd, PartialCommandGroup):
                    found = discord.utils.get(cmd.commands, name=key)
                else:
                    found = cmd.all_commands.get(key)  # type: ignore
            except AttributeError:
                string = await maybe_coro(self.subcommand_not_found, cmd, self.remove_mentions(key))  # type: ignore
                return await self.send_error_message(string)
            else:
                if found is None:
                    string = await maybe_coro(self.subcommand_not_found, cmd, self.remove_mentions(key))  # type: ignore
                    return await self.send_error_message(string)
                cmd = found

        if isinstance(cmd, PartialCommandGroup):
            return await self.send_group_help(cmd)
        else:
            return await self.send_command_help(cmd)

    async def filter_commands(
            self,
            cmd_iter: Iterable[PartialCommand],
            /,
            *,
            sort: bool = False,
            key: Optional[Callable] = lambda c: c.name,
            escape_hidden: bool = True
    ) -> List[PartialCommand]:
        """|coro|

        Overwritten to add support for HybridCommands, AppCommands, AppCommand Groups/SubCommands
        """
        resolved = []
        resolved_names = set()

        def is_hidden(cmd: PartialCommand) -> bool:  # noqa
            return cmd.hidden and escape_hidden

        for cmd in cmd_iter:
            if cmd.name in resolved_names:
                continue

            if isinstance(cmd, PartialCommandGroup):
                if isinstance(cmd, commands.Group):
                    if is_hidden(cmd):
                        continue
                for subcmd in cmd.commands:
                    if subcmd.qualified_name in resolved_names:
                        continue
                    if isinstance(subcmd, PartialCommandGroup):
                        for subsubcmd in subcmd.commands:
                            if subsubcmd.qualified_name in resolved_names:
                                continue
                            resolved.append(subsubcmd)
                            resolved_names.add(subsubcmd.qualified_name)
                    else:
                        resolved.append(subcmd)
                        resolved_names.add(subcmd.qualified_name)
            else:
                if isinstance(cmd, commands.Command):
                    if is_hidden(cmd):
                        continue
                    resolved.append(cmd)
                    resolved_names.add(cmd.name)
                else:
                    resolved.append(cmd)
                    resolved_names.add(cmd.name)

        if sort:
            return sorted(resolved, key=key)
        return resolved

    async def on_help_command_error(self, ctx: Context, error: commands.CommandError):
        if (isinstance(error, commands.CommandInvokeError)
                and isinstance(error.original, discord.HTTPException)
                and error.original.code == 50013):
            return
        await ctx.stick(False, f'**Critical:** `{str(error.args)}`')

    def get_command_signature(self, command: PartialCommand) -> str:  # noqa
        is_app_command = hasattr(command, 'parent')
        parent = (command.parent.name if command.parent else None) if is_app_command else command.full_parent_name
        aliases = '|'.join(command.aliases) if not is_app_command and len(command.aliases) > 0 else None

        alias = f'[{command.name}|{aliases}]' if aliases else command.name
        alias = f'{parent} {alias}' if parent else alias

        return f'{alias} {command.signature}' if not is_app_command else alias

    async def send_bot_help(self, mapping: Mapping[commands.Cog | None, list[PartialCommand]]):
        bot = self.context.bot

        def key(cmd: PartialCommand) -> str:
            try:
                if isinstance(cmd, app_commands.commands.Group):
                    return cmd.parent.qualified_name
                elif isinstance(cmd, app_commands.commands.Command):
                    return cmd.binding.qualified_name
                else:
                    return cmd.cog.qualified_name
            except AttributeError:
                # Escape if None but still not group to None
                return '\U0010ffff'

        entries: list[PartialCommand] = await self.filter_commands(
            self.all_commands, sort=True, key=lambda cmd: key(cmd),
            escape_hidden=not await self.context.bot.is_owner(self.context.author))

        grouped: dict[commands.Cog, list[PartialCommand]] = {}
        for name, children in itertools.groupby(entries, key=lambda cmd: key(cmd)):
            if name == '\U0010ffff':
                continue

            cog = bot.get_cog(name)
            if cog is None:
                continue

            grouped[cog] = list(children)

        await FrontHelpPaginator.start(self.context, entries=grouped, per_page=1)

    async def send_cog_help(self, cog: commands.Cog):
        entries = await self.filter_commands(
            self.get_cog_commands(cog),
            sort=True,
            escape_hidden=not await self.context.bot.is_owner(self.context.author))
        await GroupHelpPaginator.start(self.context, entries=entries, group=cog, prefix=self.context.clean_prefix)

    def common_command_formatting(self, embed: discord.Embed, command: PartialCommand):  # noqa
        is_app_command = isinstance(command, app_commands.commands.Command)

        if is_app_command:
            embed.title = self.get_command_signature(command)
            embed.description = command.description or 'No help found...'
        else:
            embed.title = f'{self.get_command_signature(command)}{' (*hidden*)' if command.hidden else ''}'
            embed.description = (
                f'{command.description}\n\n{command.help}'
                if command.description and (command.description != command.help or command.help is None)
                else command.description or command.help or 'No help found...')

        examples = command.extras.get('examples')
        if examples:
            full_name = command.name if not command.full_parent_name else f'{command.full_parent_name} {command.name}'
            text = '\n'.join(f'`{self.context.clean_prefix}{full_name} {example}`' for example in examples)
            embed.add_field(name='Examples', value=text, inline=False)

    async def send_command_help(self, command: PartialCommand):  # noqa
        if not isinstance(command, app_commands.commands.Command):
            if command.hidden and not await self.context.bot.is_owner(self.context.author):
                return await self.context.send(f'No Command called {command!r} found.')
        embed = discord.Embed(colour=formats.Colour.teal())
        self.common_command_formatting(embed, command)
        await self.context.send(embed=embed, silent=True)

    async def send_group_help(self, group: PartialCommandGroup):
        subcommands = group.commands
        if not subcommands:
            return await self.send_command_help(group)

        entries = await self.filter_commands(
            subcommands,
            sort=True,
            escape_hidden=not await self.context.bot.is_owner(self.context.author))
        if not entries:
            return await self.send_command_help(group)

        await GroupHelpPaginator.start(self.context, entries=entries, prefix=self.context.clean_prefix, group=group)

    @classmethod
    def temporary(cls, context: Context | discord.Interaction) -> 'PaginatedHelpCommand':
        self = cls()
        self.context = context
        return self


class FeedbackModal(discord.ui.Modal, title='Submit Feedback'):
    summary = discord.ui.TextInput(label='Summary', placeholder='A brief explanation of what you want')
    details = discord.ui.TextInput(label='Details', style=discord.TextStyle.long, required=False)

    def __init__(self, cog: Meta) -> None:
        super().__init__()
        self.cog: Meta = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = self.cog.feedback_channel
        if channel is None:
            await interaction.response.send_message('<:redTick:1079249771975413910> '
                                                    'Could not submit your feedback, sorry about this', ephemeral=True)
            return

        embed = self.cog.get_feedback_embed(interaction, summary=str(self.summary), details=self.details.value)
        await channel.send(embed=embed)
        await interaction.response.send_message('<:greenTick:1079249732364406854> '
                                                'Successfully submitted feedback', ephemeral=True)


class Meta(commands.Cog):
    """Some meta Commands about information about the bot and the server."""
    def __init__(self, bot: RoboHashira):
        self.bot: RoboHashira = bot

        self.old_help_command: Optional[commands.HelpCommand] = bot.help_command
        bot.help_command = PaginatedHelpCommand()
        bot.help_command.cog = self

        if not hasattr(self, '_help_autocomplete_cache'):
            self.bot.loop.create_task(self._fill_autocomplete())

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='lvl1', id=1072925290520653884)

    def cog_unload(self):
        self.bot.help_command = self.old_help_command

    async def cog_command_error(self, ctx: Context, error: commands.CommandError):
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))

    async def _fill_autocomplete(self) -> None:
        def key(command: PartialCommand) -> str:  # noqa
            cog = command.cog
            return cog.qualified_name if cog else '\U0010ffff'

        entries: list[PartialCommand] = await self.bot.help_command.filter_commands(
            self.bot.commands, sort=True, key=key)
        all_commands: dict[commands.Cog, list[PartialCommand]] = {}
        for name, children in itertools.groupby(entries, key=key):
            if name == '\U0010ffff':
                continue

            cog = self.bot.get_cog(name)
            assert cog is not None
            all_commands[cog] = sorted(children, key=lambda c: c.qualified_name)

        self._help_autocomplete_cache: Dict[commands.Cog, List[PartialCommand]] = all_commands

    @property
    def feedback_channel(self) -> Optional[discord.TextChannel]:
        guild = self.bot.get_guild(RH_MUSIC_GUILD_ID)
        if guild is None:
            return None

        return guild.get_channel(1070028934638473266)

    @staticmethod
    def get_feedback_embed(
            obj: Context | discord.Interaction, *, summary: str, details: Optional[str] = None
    ) -> discord.Embed:
        e = discord.Embed(title='Feedback', color=formats.Colour.teal())

        if details is not None:
            e.description = details
            e.title = summary[:256]
        else:
            e.description = summary

        if obj.guild is not None:
            e.add_field(name='Server', value=f'{obj.guild.name} (ID: {obj.guild.id})', inline=False)

        if obj.channel is not None:
            e.add_field(name='Channel', value=f'{obj.channel} (ID: {obj.channel.id})', inline=False)

        if isinstance(obj, discord.Interaction):
            e.timestamp = obj.created_at
            user = obj.user
        else:
            e.timestamp = obj.message.created_at
            user = obj.author

        e.set_author(name=str(user), icon_url=user.display_avatar.url)
        e.set_footer(text=f'Author ID: {user.id}')
        return e

    @_commands.command(commands.command)
    @commands.cooldown(rate=1, per=60.0, type=commands.BucketType.user)
    async def feedback(self, ctx: Context, *, content: str):
        """Sends feedback about the bot to the owner.

        The Owner will communicate with you via PM to inform
        you about the status of your request if needed.

        You can only request feedback once a minute.
        """

        channel = self.feedback_channel
        if channel is None:
            return

        e = self.get_feedback_embed(ctx, summary=content)
        await channel.send(embed=e)
        await ctx.stick(True, 'Successfully sent feedback')

    @_commands.command(app_commands.command, name='feedback')
    async def feedback_slash(self, interaction: discord.Interaction):
        """Sends feedback about the bot to the owner."""

        await interaction.response.send_modal(FeedbackModal(self))

    @_commands.command(commands.command)
    @commands.is_owner()
    async def pm(self, ctx: Context, user_id: int, *, content: str):
        """Sends a DM to a user by ID."""
        user = self.bot.get_user(user_id) or (await self.bot.fetch_user(user_id))

        fmt = (
            content + '\n\n*This is a DM sent because you had previously requested feedback or I found a bug'
            ' in a command you used, I do not monitor this DM.*'
        )
        try:
            await user.send(fmt)
        except:
            await ctx.stick(False, f'Could not PM user [`{user_id}`] by ID.')
        else:
            await ctx.stick(True, 'PM successfully sent.')

    @_commands.command(app_commands.command, name='help', guild_only=True)
    @app_commands.describe(module='Get help for a module.',
                           command='Get help for a command')
    async def _help(self, interaction: discord.Interaction, module: Optional[str] = None, command: Optional[str] = None):
        """Shows help for a command or module."""
        ctx: Context = await self.bot.get_context(interaction)
        await ctx.send_help(module or command)

    @_help.autocomplete('command')
    async def help_command_autocomplete(
            self,
            interaction: discord.Interaction,
            current: str,
    ) -> list[app_commands.Choice[str]]:
        if not hasattr(self, '_help_autocomplete_cache'):
            await interaction.response.autocomplete([])
            self.bot.loop.create_task(self._fill_autocomplete())  # noqa

        module = interaction.namespace.module
        if module is not None:
            module_commands = self._help_autocomplete_cache.get(module, [])
            commands = [c for c in module_commands if c.qualified_name == module]
        else:
            commands = list(itertools.chain.from_iterable(self._help_autocomplete_cache.values()))

        results = fuzzy.finder(current, [c.qualified_name for c in commands])
        choices = [app_commands.Choice(name=res, value=res) for res in results[:25]]
        return choices

    @_help.autocomplete('module')
    async def help_cog_autocomplete(
            self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not hasattr(self, '_help_autocomplete_cache'):
            self.bot.loop.create_task(self._fill_autocomplete())  # noqa

        cogs = self._help_autocomplete_cache.keys()
        results = fuzzy.finder(current, [c.qualified_name for c in cogs])
        return [app_commands.Choice(name=res, value=res) for res in results][:25]

    @_commands.command(commands.group, name='prefix', invoke_without_command=True)
    async def _prefix(self, ctx: Context):
        """Manages the server's custom prefixes.
        If called without a subcommand, this will list the currently set
        prefixes.
        """

        prefixes = self.bot.get_guild_prefixes(ctx.guild)

        del prefixes[1]

        e = discord.Embed(title='Prefix List', colour=self.bot.colour.teal())
        e.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
        e.set_thumbnail(url=self.bot.user.avatar.url)
        e.set_footer(text=f'{len(prefixes)} prefixes')
        e.description = '\n'.join(f'{index}. {elem}' for index, elem in enumerate(prefixes, 1))
        await ctx.send(embed=e)

    @_commands.command(_prefix.command, name='add', ignore_extra=False)
    @_commands.permissions(user=PermissionTemplate.manager)
    async def _prefix_add(self, ctx: GuildContext, prefix: Annotated[str, Prefix]):
        """Appends a prefix to the list of custom prefixes.
        Previously set prefixes are not overridden.
        To have a word prefix, you should quote it and end it with
        a space, e.g. 'hello ' to set the prefix to 'hello '. This
        is because Discord removes spaces when sending messages so
        the spaces are not preserved.
        Multi-word prefixes must be quoted also.
        You must have Manage Server permission to use this command.
        """

        current_prefixes = self.bot.get_raw_guild_prefixes(ctx.guild.id)
        current_prefixes.append(prefix)
        try:
            await self.bot.set_guild_prefixes(ctx.guild, current_prefixes)
        except Exception as e:
            await ctx.stick(False, f'{e}')
        else:
            await ctx.stick(True, 'Prefix added.')

    @_commands.command(_prefix.command, name='remove', aliases=['delete'], ignore_extra=False)
    @_commands.permissions(user=PermissionTemplate.manager)
    async def _prefix_remove(self, ctx: GuildContext, prefix: Annotated[str, Prefix]):
        """Removes a prefix from the list of custom prefixes.
        This is the inverse of the 'prefix add' command. You can
        use this to remove prefixes from the default set as well.
        You must have Manage Server permission to use this command.
        """

        current_prefixes = self.bot.get_raw_guild_prefixes(ctx.guild.id)

        try:
            current_prefixes.remove(prefix)
        except ValueError:
            return await ctx.stick(False, 'I do not have this prefix registered.')

        try:
            await self.bot.set_guild_prefixes(ctx.guild, current_prefixes)
        except Exception as e:
            await ctx.stick(False, f'{e}')
        else:
            await ctx.stick(True, 'Prefix removed.')

    @_commands.command(_prefix.command, name='clear')
    @_commands.permissions(user=PermissionTemplate.manager)
    async def _prefix_clear(self, ctx: GuildContext):
        """Removes all custom prefixes.
        After this, the bot will listen to only mention prefixes.
        You must have Manage Server permission to use this command.
        """

        await self.bot.set_guild_prefixes(ctx.guild, [])
        await ctx.stick(True, 'Cleared all prefixes.')

    @_commands.command(name='ping', description='Get the bots latency.')
    async def ping(self, ctx: Context):
        """Shows some Client and API latency information."""

        message = None

        api_readings: List[float] = []
        websocket_readings: List[float] = []

        for _ in range(6):
            text = '*Calculating round-trip time...*\n\n'
            text += '\n'.join(
                f'Reading `{index + 1}`: `{reading * 1000:.2f}ms`' for index, reading in enumerate(api_readings))

            if api_readings:
                average, stddev = mean_stddev(api_readings)

                text += f'\n\n**Average:** `{average * 1000:.2f}ms` \N{PLUS-MINUS SIGN} `{stddev * 1000:.2f}ms`'
            else:
                text += '\n\n*No readings yet.*'

            if websocket_readings:
                average = sum(websocket_readings) / len(websocket_readings)

                text += f'\n**Websocket latency:** `{average * 1000:.2f}ms`'
            else:
                text += f'\n**Websocket latency:** `{self.bot.latency * 1000:.2f}ms`'

            if _ == 5:
                gateway_url = await self.bot.http.get_gateway()
                start = time.monotonic()
                async with aiohttp.ClientSession() as session:
                    async with session.get(f'{gateway_url}/ping') as resp:
                        end = time.monotonic()
                        gateway_ping = (end - start) * 1000

                text += f'\n**Gateway latency:** `{gateway_ping:.2f}ms`'

            if message:
                before = time.perf_counter()
                await message.edit(embed=discord.Embed(title='Pong!...',
                                                             description=text,
                                                             color=formats.Colour.teal()))
                after = time.perf_counter()

                api_readings.append(after - before)
            else:
                before = time.perf_counter()
                message = await ctx.send(embed=discord.Embed(title='Pong!...',
                                                             description=text,
                                                             color=formats.Colour.teal()))
                after = time.perf_counter()

                api_readings.append(after - before)

            if self.bot.latency > 0.0:
                websocket_readings.append(self.bot.latency)

    @_commands.command(name='vote', description='Shows current available vote links')
    async def vote(self, ctx: Context):
        """Shows current available vote links for the bot."""
        embed = discord.Embed(title='Vote',
                              description='[Top.gg](https://top.gg/bot/1062083962773717053/vote)\n'
                                          '[discord-botlist.eu](https://discord-botlist.eu/vote/1062083962773717053)\n'
                                          '[discordbotlist.com](https://discordbotlist.com/bots/RoboHashira/upvote)\n'
                                          '[Void Bots](https://voidbots.net/bot/1062083962773717053/vote)',
                              color=self.bot.colour.teal())
        embed.set_thumbnail(url=self.bot.user.avatar.url)
        await ctx.send(embed=embed)

    @_commands.command(aliases=['invite'])
    async def join(self, ctx: Context):
        """Posts my invite to allow you to invite me"""
        perms = discord.Permissions.none()
        perms.read_messages = True
        perms.external_emojis = True
        perms.send_messages = True
        perms.manage_roles = True
        perms.manage_channels = True
        perms.manage_messages = True
        perms.embed_links = True
        perms.read_message_history = True
        perms.attach_files = True
        perms.add_reactions = True
        perms.connect = True
        perms.speak = True
        perms.use_voice_activation = True
        perms.use_application_commands = True
        perms.priority_speaker = True
        await ctx.send(f'<{discord.utils.oauth_url(self.bot.application_id, permissions=perms)}>')


async def setup(bot: RoboHashira):
    await bot.add_cog(Meta(bot))
