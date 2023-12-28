import functools
import sys
from contextlib import suppress
from typing import Callable, TypeVar

import discord
from discord import Forbidden, NotFound, app_commands
from discord.ext import commands
from fuzzywuzzy.string_processing import StringProcessor

from ..utils.context import GuildContext, Context

T = TypeVar('T')
PY3 = sys.version_info[0] == 3


# +++ Fuzzy matching +++

def validate_string(s: str) -> bool:
    """
    Check input has length and that length > 0

    :param s:
    :return: True if len(s) > 0 else False
    """
    try:
        return len(s) > 0
    except TypeError:
        return False


def check_for_equivalence(func: Callable[[str, str], T]) -> Callable[[str], T]:
    @functools.wraps(func)
    def decorator(*args, **kwargs):
        if args[0] == args[1]:
            return 100
        return func(*args, **kwargs)
    return decorator


def check_for_none(func: Callable[[str, str], T]) -> Callable[[str], T]:
    @functools.wraps(func)
    def decorator(*args, **kwargs):
        if args[0] is None or args[1] is None:
            return 0
        return func(*args, **kwargs)
    return decorator


def check_empty_string(func: Callable[[str, str], T]) -> Callable[[str], T]:
    @functools.wraps(func)
    def decorator(*args, **kwargs):
        if len(args[0]) == 0 or len(args[1]) == 0:
            return 0
        return func(*args, **kwargs)
    return decorator


bad_chars = str("").join([chr(i) for i in range(128, 256)])
if PY3:
    translation_table = dict((ord(c), None) for c in bad_chars)
    unicode = str


def asciionly(s: str | bytes) -> str:
    if PY3:
        return s.translate(translation_table)
    else:
        return s.translate(None, bad_chars)


def asciidammit(s: str) -> str | bytes:
    if type(s) is str:
        return asciionly(s)
    elif type(s) is unicode:
        return asciionly(s.encode('ascii', 'ignore'))
    else:
        return asciidammit(unicode(s))


def make_type_consistent(s1: str, s2: str) -> tuple[str, str]:
    """If both objects aren't either both string or unicode instances force them to unicode"""
    if isinstance(s1, str) and isinstance(s2, str):
        return s1, s2

    elif isinstance(s1, unicode) and isinstance(s2, unicode):
        return s1, s2

    else:
        return unicode(s1), unicode(s2)


def full_process(s: str, force_ascii=False) -> str:
    """Process string by
        -- removing all but letters and numbers
        -- trim whitespace
        -- force to lower case
        if force_ascii == True, force convert to ascii"""

    if force_ascii:
        s = asciidammit(s)
    string_out = StringProcessor.replace_non_letters_non_numbers_with_whitespace(s)
    string_out = StringProcessor.to_lower_case(string_out)
    string_out = StringProcessor.strip(string_out)
    return string_out


def intr(n: float) -> int:
    """Returns a correctly rounded integer"""
    return int(round(n))


# ++++ Checks ++++ #


def is_player_connected():
    async def predicate(ctx: Context) -> bool:
        if not ctx.voice_client or not ctx.voice_client.channel:
            await ctx.stick(False, "I'm not connected to a voice channel right now.", ephemeral=True)
            return False

        return True

    return commands.check(predicate)


def is_player_playing():
    async def predicate(ctx: Context) -> bool:
        if not ctx.voice_client or not ctx.voice_client.playing:  # noqa
            await ctx.stick(False, "I'm not playing anything right now.", ephemeral=True)
            return False

        return True

    return commands.check(predicate)


def is_dj(member) -> bool:
    """Checks if the Member has the DJ Role."""
    role = discord.utils.get(member.guild.roles, name="DJ")
    if role in member.roles:
        return True
    return False


# Decorator Checks


def is_listen_together():
    """Checks if a listen together activity is active."""
    async def predicate(ctx):
        if ctx.voice_client:
            if ctx.voice_client.queue.listen_together.enabled:
                await ctx.send(
                    '<:redTick:1079249771975413910> Please stop the listen-together activity before use this Command.',
                    ephemeral=True)
                return False
        return True

    return commands.check(predicate)


def is_author_connected():
    """Checks if the author is connected to a Voice Channel."""
    async def predicate(ctx: Context) -> bool:
        assert isinstance(ctx.user, discord.Member)

        author_vc = ctx.user.voice and ctx.user.voice.channel
        bot_vc = ctx.guild.me.voice and ctx.guild.me.voice.channel

        if is_dj(ctx.user) and bot_vc and (not author_vc):
            return True
        if (author_vc and bot_vc) and (author_vc == bot_vc):
            if ctx.user.voice.deaf or ctx.user.voice.self_deaf:
                await ctx.send(
                    "<:redTick:1079249771975413910> You are deafened, please undeafen yourself to use this command.",
                    ephemeral=True
                )
                return False
            return True
        if (not author_vc and bot_vc) or (author_vc and bot_vc):
            await ctx.send(
                f"<:redTick:1079249771975413910> You must be in {bot_vc.mention} to use this command.",
                ephemeral=True
            )
            return False
        if not author_vc:
            await ctx.send(
                "<:redTick:1079249771975413910> You must be in a voice channel to use this command.",
                ephemeral=True
            )
            return False
        return True

    return commands.check(predicate)


def isDJorAdmin():
    """Checks if the user has the DJ role or is an Admin."""
    async def predicate(ctx):
        with suppress(AttributeError, Forbidden, NotFound):
            djRole = discord.utils.get(ctx.guild.roles, name="DJ")

            if djRole in ctx.author.roles or ctx.author.guild_permissions.administrator:
                return True
            await ctx.send(
                '<:redTick:1079249771975413910> You need to be an Admin or DJ to use this Command.',
                ephemeral=True)
            return False

    return commands.check(predicate)


def isInSameChannel():
    """Checks if the user is in the same Voice Channel as the Bot."""
    async def predicate(ctx):
        if ctx.author.voice is not None:
            if ctx.voice_client is not None:
                if ctx.author.voice.channel == ctx.voice_client.channel:
                    return True
        await ctx.send(
            '<:redTick:1079249771975413910> You need to be in the same Voice Channel as the Bot to use this command.',
            ephemeral=True)
        return False

    return commands.check(predicate)


async def check_permissions(ctx: GuildContext, perms: dict[str, bool], *, check=all):
    # noinspection PyProtectedMember
    is_owner = await ctx.bot.is_owner(ctx.author._user)
    if is_owner:
        return True

    resolved = ctx.channel.permissions_for(ctx.author)
    return check(getattr(resolved, name, None) == value for name, value in perms.items())


def has_permissions(*, check=all, **perms: bool):
    async def pred(ctx: GuildContext):
        return await check_permissions(ctx, perms, check=check)

    return commands.check(pred)


async def check_guild_permissions(ctx: GuildContext, perms: dict[str, bool], *, check=all):
    # noinspection PyProtectedMember
    is_owner = await ctx.bot.is_owner(ctx.author._user)
    if is_owner:
        return True

    if ctx.guild is None:
        return False

    resolved = ctx.author.guild_permissions
    return check(getattr(resolved, name, None) == value for name, value in perms.items())


def has_guild_permissions(*, check=all, **perms: bool):
    async def pred(ctx: GuildContext):
        return await check_guild_permissions(ctx, perms, check=check)

    return commands.check(pred)


def hybrid_permissions_check(**perms: bool) -> Callable[[T], T]:
    async def pred(ctx: GuildContext):
        return await check_guild_permissions(ctx, perms)

    def decorator(func: T) -> T:
        commands.check(pred)(func)
        app_commands.default_permissions(**perms)(func)
        return func

    return decorator


def is_manager():
    return hybrid_permissions_check(manage_guild=True)


def is_mod():
    return hybrid_permissions_check(ban_members=True, manage_messages=True)


def is_admin():
    return hybrid_permissions_check(administrator=True)


def is_in_guilds(*guild_ids: int):
    def predicate(ctx: GuildContext) -> bool:
        guild = ctx.guild
        if guild is None:
            return False
        return guild.id in guild_ids

    return commands.check(predicate)
