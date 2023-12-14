from __future__ import annotations

import datetime

from typing import Any, Iterable, Optional, Sequence, Self

import asyncpg
import discord

from cogs.utils import converters


class MaybeAcquire:
    def __init__(self, connection: Optional[asyncpg.Connection], *, pool: asyncpg.Pool) -> None:
        self.connection: Optional[asyncpg.Connection] = connection
        self.pool: asyncpg.Pool = pool
        self._cleanup: bool = False

    async def __aenter__(self) -> asyncpg.Connection:
        if self.connection is None:
            self._cleanup = True
            self._connection = c = await self.pool.acquire()
            return c
        return self.connection

    async def __aexit__(self, *args) -> None:
        if self._cleanup:
            await self.pool.release(self._connection)


class plural:
    """A format spec which handles making words plural or singular based off of its value.

    Credit: https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/utils/formats.py#L8-L18
    """

    def __init__(self, sized: int, pass_content: bool = False):
        self.sized: int = sized
        self.pass_content: bool = pass_content

    def __format__(self, format_spec: str) -> str:
        s = self.sized
        singular, sep, plural = format_spec.partition('|')
        plural = plural or f'{singular}s'
        if self.pass_content:
            return singular if abs(s) == 1 else plural

        if abs(s) != 1:
            return f'{s} {plural}'
        return f'{s} {singular}'


def readable_time(seconds: int | float, decimal: bool = False, short: bool = False) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    days, hours = divmod(hours, 24)
    months, days = divmod(days, 30)  # Approximately
    years, months = divmod(months, 12)

    attrs = {
        "y" if short else "year": years,
        "mo" if short else "month": months,
        "d" if short else "day": days,
        "hr" if short else "hour": hours,
        "m" if short else "minute": minutes,
        "s" if short else "second": seconds,
    }

    output = []
    for unit, value in attrs.items():
        value = round(value, 2 if decimal else None)
        if value > 0:
            output.append(f"{value}{' ' * (not short)}{unit}{('s' if value != 1 else '') * (not short)}")

    return ", ".join(output)


def shorten_number(number: int | float) -> str:
    number = float(f"{number:.3g}")
    magnitude = 0

    while abs(number) >= 1000:
        magnitude += 1
        number /= 1000

    return f"{f'{number:f}'.rstrip('0').rstrip('.')}{['', 'K', 'M', 'B', 'T'][magnitude]}"


class Colour(discord.Colour):

    @classmethod
    def darker_red(cls) -> Self:
        return cls(0xE32636)

    @classmethod
    def transparent(cls) -> Self:
        return cls(formats.Colour.teal())

    @classmethod
    def lime_green(cls) -> Self:
        return cls(0x3AFF76)

    @classmethod
    def light_red(cls) -> Self:
        return cls(0xFF6666)

    @classmethod
    def light_orange(cls) -> Self:
        return cls(0xFF8000)

    @classmethod
    def electric_violet(cls) -> Self:
        return cls(0x9b00ff)

    @classmethod
    def teal(cls) -> Self:
        return cls(0x1f5b87)


def merge(rm_dup: bool = False, *lists) -> list:
    """
    Merge two or more lists into one.

    Parameters:
    rm_dup (bool): If True, remove duplicates from the merged list.
    *lists: Lists to be merged.

    Returns:
    Merged list.
    """
    merged_list = []
    for lst in lists:
        if rm_dup:
            merged_list.extend([i for i in lst if i not in merged_list])
        else:
            merged_list.extend(lst)

    return merged_list


def human_join(seq: Sequence[str], delim: str = ', ', final: str = 'or') -> str:
    size = len(seq)
    if size == 0:
        return ''

    if size == 1:
        return seq[0]

    if size == 2:
        return f'{seq[0]} {final} {seq[1]}'

    return delim.join(seq[:-1]) + f' {final} {seq[-1]}'


class TabularData:
    def __init__(self):
        self._widths: list[int] = []
        self._columns: list[str] = []
        self._rows: list[list[str]] = []

    def set_columns(self, columns: list[str]):
        self._columns = columns
        self._widths = [len(c) + 2 for c in columns]

    def add_row(self, row: Iterable[Any]) -> None:
        rows = [str(r) for r in row]
        self._rows.append(rows)
        for index, element in enumerate(rows):
            width = len(element) + 2
            if width > self._widths[index]:
                self._widths[index] = width

    def add_rows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.add_row(row)

    def render(self) -> str:
        """Renders a table in rST format.
        Example:
        +-------+-----+
        | Name  | Age |
        +-------+-----+
        | Alice | 24  |
        |  Bob  | 19  |
        +-------+-----+
        """

        sep = '+'.join('-' * w for w in self._widths)
        sep = f'+{sep}+'

        to_draw = [sep]

        def get_entry(d):
            elem = '|'.join(f'{e:^{self._widths[i]}}' for i, e in enumerate(d))
            return f'|{elem}|'

        to_draw.append(get_entry(self._columns))
        to_draw.append(sep)

        for row in self._rows:
            to_draw.append(get_entry(row))

        to_draw.append(sep)
        return '\n'.join(to_draw)


class AnsiTabularData(TabularData):
    def __init__(self):
        super().__init__()
        self._widths: list[int] = []
        self._columns: list[str] = []
        self._rows: list[list[str]] = []

    def render(self) -> str:
        """Renders a table in rST format.
        ( ANSI color formatted )
        Example:
        +-------+-----+
        | Name  | Age |
        +-------+-----+
        | Alice | 24  |
        |  Bob  | 19  |
        +-------+-----+
        """

        sep = '+'.join('-' * w for w in self._widths)
        sep = f'+{sep}+'

        to_draw = ['```ansi', sep]

        def get_entry(d):
            elem = '|'.join(f'\u001b[0;40m{e:^{self._widths[i]}}\u001b[0;0m' for i, e in enumerate(d))
            return f'|{elem}|'

        def get_row_entry(d):
            elem = '|'.join(f'\u001b[0;34m{e:^{self._widths[i]}}\u001b[0;0m' for i, e in enumerate(d))
            return f'|{elem}|'

        to_draw.append(get_entry(self._columns))
        to_draw.append(sep)

        for row in self._rows:
            to_draw.append(get_row_entry(row))

        to_draw.append(sep)
        to_draw.append('```')
        return '\n'.join(to_draw)


def format_dt(dt: datetime.datetime, style: Optional[str] = None) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)

    if style is None:
        return f'<t:{int(dt.timestamp())}>'
    return f'<t:{int(dt.timestamp())}:{style}>'


def truncate(text: str, length: int) -> str:
    if len(text) > length:
        return text[:length - 1] + "…"
    return text


def truncate_iterable(iterable: Iterable[Any], length: int, attribute: str = None) -> str:
    if len(iterable) > length:  # type: ignore
        return ", ".join(iterable[:length]) + ", …"
    return ", ".join(iterable)


def WrapList(list_: list, length: int):
    def chunks(seq, size):
        for i in range(0, len(seq), size):
            yield seq[i: i + size]

    return list(chunks(list_, length))


def get_shortened_string(length: int, start: int, string: str) -> str:
    full_length = len(string)
    if full_length <= 100:
        return string

    _id, _, remaining = string.partition(' - ')
    start_index = len(_id) + 3
    max_remaining_length = 100 - start_index

    end = start + length
    if start < start_index:
        start = start_index

    if end < 100:
        if full_length > 100:
            return string[:99] + '…'
        return string[:100]

    has_end = end < full_length
    excess = (end - start) - max_remaining_length + 1
    if has_end:
        return f'[{_id}] …{string[start + excess + 1:end]}…'
    return f'[{_id}] …{string[start + excess:end]}'


def player_stamp(length: float, position: float) -> str:
    convertable = [converters.convert_duration(position if not position < 0 else 0.0),
                   converters.VisualStamp(0, length, position),
                   converters.convert_duration(length)]
    return ' '.join(convertable)
