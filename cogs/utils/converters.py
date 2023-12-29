import sys
from types import ModuleType

from discord import app_commands
import time
import inspect
import os
import re
from io import BufferedIOBase, BytesIO
from typing import Any, List, Iterable, Sequence, Union, TYPE_CHECKING
from urllib.parse import urlparse

import aiohttp
import discord
from discord.ext import commands
import datetime as dt

from cogs.utils import errors

if TYPE_CHECKING:
    from cogs.utils.context import GuildContext
else:
    GuildContext = 'GuildContext'

MENTION_REGEX = re.compile(r'<@(!?)([0-9]*)>')

defaultBands = [{'band': i, 'gain': 0.0} for i in range(15)]


def get_asset_url(obj: Union[discord.Guild, discord.User, discord.Member, discord.ClientUser]) -> str:
    if isinstance(obj, discord.Guild):
        if not obj.icon:
            return ''
        return obj.icon.url
    if obj.avatar:
        return obj.avatar.url
    if isinstance(obj, (discord.Member, discord.ClientUser)):
        if obj.display_avatar:
            return obj.display_avatar.url


class NamedDict:
    def __init__(self, name: str = 'NamedDict', layer: dict = {}):
        self.__name__ = name
        self.__dict__.update(layer)
        self.__dict__['__shape_set__'] = 'shape' in layer

    def __len__(self):
        return len(self.__dict__)

    def __repr__(self):
        return f'{self.__name__}(%s)' % ', '.join(
            ('%s=%r' % (k, v) for k, v in self.__dict__.items() if not k.startswith('_')))

    def __getattr__(self, attr):
        if attr == 'shape':
            if not self.__dict__['__shape_set__']:
                return None
        try:
            return self.__dict__[attr]
        except KeyError:
            setattr(self, attr, NamedDict())
            return self.__dict__[attr]

    def __setattr__(self, key, value):
        self.__dict__[key] = value

    def _to_dict(self, include_names: bool = False):
        data = {}
        for k, v in self.__dict__.items():
            if isinstance(v, NamedDict):
                data[k] = v._to_dict(include_names=include_names)
            else:
                if k != '__shape_set__':
                    if k == '__name__' and not include_names:
                        continue
                    data[k] = v
        return data

    @classmethod
    def _from_dict(cls, data: dict):
        named = cls(name=data.pop('__name__', 'NamedDict'))
        _dict = named.__dict__
        for k, v in data.items():
            if isinstance(v, dict):
                _dict[k] = cls._from_dict(v)
            else:
                _dict[k] = v
        return named


def next_path(path, pattern):
    """
    Finds the next free path in a sequentially named list of files

    e.g. path_pattern = 'file-%s.txt':

    file-1.txt
    file-2.txt
    file-3.txt

    Runs in log(n) time when n is the number of existing files in sequence
    """
    i = 1

    while os.path.exists(path + pattern % i):
        i = i * 2

    a, b = (i // 2, i)
    while a + 1 < b:
        c = (a + b) // 2
        a, b = (c, b) if os.path.exists(path + pattern % c) else (a, c)

    return path + pattern % b


def group_by_len(items: Sequence[str], type: bool = False) -> Iterable[str]:
    if type:
        max_len = 15

        start, count = 0, 0

        for end, item in enumerate(items):
            if count >= max_len:
                yield '\n'.join(items[start: end])
                count = 0
                start = end
            count += 1

        if count > 0:
            yield '\n'.join(items[start:])
    else:
        max_len = 3000

        start, count = 0, 0

        for end, item in enumerate(items):
            n = len(item)
            if n + count >= max_len:
                yield '\n'.join(items[start: end])
                count = 0
                start = end
            count += n

        if count > 0:
            yield '\n'.join(items[start:])


def format_list(items: list, seperator: str = 'or', brackets: str = ""):
    new_items = []
    for i in items:
        if not re.match(MENTION_REGEX, i):
            new_items.append(f'{brackets}{i}{brackets}')
        else:
            new_items.append(str(i))

    msg = ', '.join(list(new_items)[:-1]) + f' {seperator} ' + list(new_items)[-1]
    return msg


def convert_duration(milliseconds) -> time:
    seconds = milliseconds / 1000
    formaT = '%H:%M:%S' if seconds >= 3600 else '%M:%S'
    return time.strftime(formaT, time.gmtime(seconds))


def ascii_list(items: List[str]) -> List[str]:
    texts = []
    for item in items:
        if item == items[-1]:
            text = f'└─ {item}'
        else:
            text = f'├─ {item}'
        texts.append(text)

    return texts


async def aenumerate(asequence, start=0):
    """Asynchronously enumerate an async iterator from a given start value"""
    n = start
    async for elem in asequence:
        yield n, elem
        n += 1


def VisualStamp(key_min: float, key_max: float, key_current: float, key_full: int = 32) -> str:
    """
    Example Output:
    ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬🔘▬▬▬▬▬▬▬
    """
    if key_min == key_current:
        before = key_max
        after = key_min
    else:
        before = key_min + key_current
        after = key_max - key_current
    for i in range(int(key_min + 2), int(key_max)):
        if len(int(before / i) * '▬' + '🔘' + int(
                after / i) * '▬') <= key_full:
            return str(int(before / i) * '▬' + '🔘' + int(
                after / i) * '▬')


def EqualizerStamp(player) -> str:
    """
    Example Output:

    1  2  3  4  5  6  7  8  9  10 11 12 13 14 15
    ║  ║  ║  ║  ║  ║  ║  ║  ║  ║  ║  ║  ║  ║  ║
    ╬  ╬  ╬  ╬  ╬  ╬  ╬  ╬  ╬  ╬  ╬  ╬  ╬  ╬  ╬
    ║  ║  ║  ║  ║  ║  ║  ║  ║  ║  ║  ║  ║  ║  ║
    """
    output = [' '.join(str(i).center(2) for i in range(1, 16))]

    try:
        bands = player.filter.equalizer.bands
    except AttributeError:
        bands = defaultBands

    output.append(' '.join(s.center(2) for s in ('╬' if k['gain'] > 0 else '║' for k in bands)))
    output.append(' '.join(s.center(2) for s in ('╬' if k['gain'] == 0 else '║' for k in bands)))
    output.append(' '.join(s.center(2) for s in ('╬' if k['gain'] < 0 else '║' for k in bands)))

    return '\n'.join(output)


def convert_time(seconds: float):
    return dt.timedelta(seconds=round(seconds))


def member_count(g, val):
    mc = 0
    for member in g:
        if val is False:
            if not member.bot:
                mc = mc + 1
        else:
            if member.bot:
                mc = mc + 1
    return mc


URL_REGEX = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')


class URLObject:
    def __init__(self, url: str):
        if not URL_REGEX.match(url):
            raise TypeError(f'Invalid url provided')
        self.url = url
        self.filename = url.split('/')[-1]

    async def read(self, *, session=None) -> bytes:
        """Reads this asset."""
        _session = session or aiohttp.ClientSession()
        try:
            async with _session.get(self.url) as resp:
                if resp.status == 200:
                    return await resp.read()
                elif resp.status == 404:
                    raise discord.NotFound(resp, 'asset not found')
                elif resp.status == 403:
                    raise discord.Forbidden(resp, 'cannot retrieve asset')
                else:
                    raise discord.HTTPException(resp, 'failed to get asset')
        finally:
            if not session:
                await _session.close()

    async def save(
            self,
            fp: BufferedIOBase | os.PathLike[Any],
            *,
            data: bytes = None,
            seek_begin: bool = True,
    ) -> int:
        """Saves to an object or buffer."""
        data = data or await self.read()
        if isinstance(fp, BufferedIOBase):
            written = fp.write(data)
            if seek_begin:
                fp.seek(0)
            return written

        with open(fp, 'wb') as f:
            return f.write(data)

    @property
    def spoiler(self):
        """Wether the file is a spoiler"""
        return self.name.startswith('SPOILER_')

    # noinspection PyAttributeOutsideInit
    @spoiler.setter
    def spoiler(self, value: bool):
        if value != self.spoiler:
            if value is True:
                self.name = f'SPOILER_{self.name}'
            else:
                self.name = self.name.split('_', maxsplit=1)[1]

    async def to_file(self, *, session: aiohttp.ClientSession = None):
        return discord.File(
            BytesIO(await self.read(session=session)), self.name, spoiler=False
        )


class FalseInt(object):
    @staticmethod
    def convert(argument: str):
        try:
            id = int(argument) or float(argument)
        except ValueError:
            try:
                id = int(argument, base=10)
            except ValueError:
                raise commands.BadArgument(f'`{argument}` is not a valid number or float.') from None
        return id


class Prefix(commands.Converter):
    async def convert(self, ctx: GuildContext, argument: str) -> str:  # type: ignore
        user_id = ctx.bot.user.id
        if argument.startswith((f'<@{user_id}>', f'<@!{user_id}>')):
            raise commands.BadArgument('That is a reserved prefix already in use.')
        if len(argument) > 150:
            raise commands.BadArgument('That prefix is too long.')
        return argument


class URLConverter(commands.Converter):
    async def convert(
            self, ctx: commands.Context | discord.Interaction, argument: str
    ) -> str:
        parsed_url = urlparse(argument)

        if str(parsed_url.netloc).split(':')[0] in (
                '127.0.0.1',
                'localhost',
                '0.0.0.0',
        ) and not await ctx.bot.is_owner(ctx.author):
            raise commands.BadArgument('Invalid URL')

        return argument


class SpecificUserConverter(commands.Converter):
    """User Converter class that only supports IDs and mentions"""

    async def _get_user(self, bot: commands.Bot, argument: int):
        user = bot.get_user(argument)
        if user:
            return user
        return await bot.fetch_user(argument)

    async def convert(self, ctx: commands.Context, argument: str):
        is_digits = all(char.isdigit() for char in argument)

        if is_digits:
            if user := await self._get_user(ctx.bot, int(argument)):
                return user

        if match := re.match(r'<@!?([0-9]+)>', argument):
            if user := await self._get_user(ctx.bot, int(match.group(1))):
                return user

        raise commands.BadArgument('Failed to convert argument to user')


class QueueIndex(app_commands.Transformer):
    async def transform(self, interaction: discord.Interaction, value: str) -> int:
        try:
            index = int(value)
        except ValueError:
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title='Error',
                    description='Please provide a valid integer',
                    color=discord.Color.red()
                )
            )

        if index <= 0:
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title='Error',
                    description='Please provide a valid integer that is greater than 0.',
                    color=discord.Color.red()
                )
            )
        if index > (total_tracks := interaction.guild.voice_client.queue.count):  # noqa
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title='Error',
                    description=f'There are only {total_tracks} tracks in the queue.',
                    color=discord.Color.red()
                )
            )

        return index


class FileConverter(commands.Converter):
    async def convert(
            self, ctx: commands.Context | discord.Interaction, file: str = None
    ) -> discord.Attachment | URLObject:
        if file is None:
            if ctx.message.attachments:
                attachment = ctx.message.attachments[0]
            elif ctx.message.reference:
                if ctx.message.reference.resolved.attachments:
                    attachment = ctx.message.reference.resolved.attachments[0]
                else:
                    raise commands.MissingRequiredArgument(
                        inspect.Parameter('file', inspect.Parameter.KEYWORD_ONLY)
                    )
            else:
                raise commands.MissingRequiredArgument(
                    inspect.Parameter('file', inspect.Parameter.KEYWORD_ONLY)
                )
        else:
            attachment = URLObject(await URLConverter().convert(ctx, file))

        return attachment


class ModuleConverter(commands.Converter[ModuleType]):
    """A converter interface to resolve imported modules."""

    async def convert(self, ctx: commands.Context, argument: str) -> ModuleType:
        """Converts a name into a :class:`ModuleType` object."""
        argument = argument.lower().strip()
        module = sys.modules.get(argument, None)

        icon = '\N{OUTBOX TRAY}' if ctx.invoked_with == 'ml' else '\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}'

        if not module:
            raise errors.BadArgument(f'{icon}\N{WARNING SIGN} `{argument!r}` is not a valid module.')
        return module