import asyncio
import copy
import io
import logging
import os
import shutil
import subprocess
import textwrap
import traceback
import time
from typing import Any, Callable, Union, Awaitable, Self, Optional, List

import discord
import pygit2
from asyncpg import Record
from discord.ext import commands
from contextlib import redirect_stdout

from .utils import formats, constants, _commands, context
from .utils.context import Context
from bot import RoboHashira
from .utils.paginator import BasePaginator, TextSource

log = logging.getLogger(__name__)


class PerformanceMocker:
    """A mock object that can also be used in await expressions."""

    def __init__(self):
        self.loop = asyncio.get_running_loop()

    @property
    def permissions_for(self) -> discord.Permissions:
        perms = discord.Permissions.all()
        perms.administrator = False
        perms.embed_links = False
        perms.add_reactions = False
        return perms

    def __getattr__(self, attr: str) -> Self:
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> Self:
        return self

    def __repr__(self) -> str:
        return '<PerformanceMocker>'

    def __await__(self):
        future: asyncio.Future[Self] = self.loop.create_future()
        future.set_result(self)
        return future.__await__()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> Self:
        return self

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False


class Admin(commands.Cog):
    """Admin commands for the bot owner."""

    def __init__(self, bot: RoboHashira):
        self.bot: RoboHashira = bot
        self.sessions: set[int] = set()
        self._last_result: Optional[Any] = None

    async def run_process(self, command: str) -> list[str]:
        try:
            process = await asyncio.create_subprocess_shell(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            result = await process.communicate()
        except NotImplementedError:
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            result = await self.bot.loop.run_in_executor(None, process.communicate)

        return [output.decode() for output in result]

    def cleanup_code(self, content: str) -> str:
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith('```') and content.endswith('```'):
            return '\n'.join(content.split('\n')[1:-1])

        # remove `foo`
        return content.strip('` \n')

    @staticmethod
    def get_syntax_error(e: SyntaxError) -> str:
        if e.text is None:
            return f'```py\n{e.__class__.__name__}: {e}\n```'
        return f'```py\n{e.text}{'^':>{e.offset}}\n{e.__class__.__name__}: {e}```'

    async def cog_check(self, ctx: Context) -> bool:
        return await self.bot.is_owner(ctx.author)

    @_commands.command(commands.command, hidden=True)
    async def syncrepo(self, ctx: Context):
        try:
            path = os.path.join(constants.BOT_BASE_FOLDER, '/rendering/repo/')
            for root, dirs, files in os.walk(path):
                for f in files:
                    os.unlink(os.path.join(root, f))
                for d in dirs:
                    shutil.rmtree(os.path.join(root, d))

            pygit2.clone_repository('https://github.com/klappstuhlpy/RoboHashira', path)
        except:
            await ctx.send(f'```py\n{traceback.format_exc()}```')
        finally:
            await ctx.stick(True)

    @_commands.command(commands.command, hidden=True)
    async def maintenance(self, ctx: Context):
        if self.bot.maintenance.get('maintenance') is True:
            await self.bot.maintenance.put('maintenance', False)
            await ctx.send('<:greenTick:1079249732364406854> Maintenance mode disabled.', ephemeral=True)
            await self.bot.change_presence(activity=discord.Activity(name=f'{self.bot.full_member_count} users',
                                                                     type=discord.ActivityType.listening))
        else:
            await self.bot.maintenance.put('maintenance', True)
            await ctx.send('<:greenTick:1079249732364406854> Maintenance mode enabled.', ephemeral=True)
            await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name='🛠️ Maintenance Mode'))

    @_commands.command(commands.command, hidden=True, name='eval')
    async def _eval(self, ctx: Context, *, body: str):
        """Evaluates a code"""

        env = {
            'bot': self.bot,
            'self': self,
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': ctx.message,
            '_': self._last_result,
        }

        env.update(globals())

        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, '  ')}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await ctx.send(f'```py\n{e.__class__.__name__}: {e}\n```')

        func = env['func']
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except Exception as e:
            value = stdout.getvalue()
            await ctx.send(f'```py\n{value}{traceback.format_exc()}\n```')
        else:
            value = stdout.getvalue()
            try:
                await ctx.message.add_reaction('\u2705')
            except:
                pass

            if ret is None:
                if value:
                    await ctx.send(f'```py\n{value}\n```')
            else:
                self._last_result = ret
                await ctx.send(f'```py\n{value}{ret}\n```')

    @_commands.command(commands.command, hidden=True,
                      description='Checks the timing of a command, attempting to suppress HTTP and DB calls.')
    async def perf(self, ctx: Context, *, command: str):
        """Checks the timing of a command, attempting to suppress HTTP and DB calls."""
        try:
            msg = copy.copy(ctx.message)
            msg.content = ctx.prefix + command

            new_ctx = await self.bot.get_context(msg, cls=type(ctx))

            new_ctx._state = PerformanceMocker()
            new_ctx.channel = PerformanceMocker()

            if new_ctx.command is None:
                return await ctx.send('No command found')

            start = time.perf_counter()
            try:
                await new_ctx.command.invoke(new_ctx)
            except commands.CommandError:
                end = time.perf_counter()
                success = False
                try:
                    await ctx.send(f'```py\n{traceback.format_exc()}\n```')
                except discord.HTTPException:
                    pass
            else:
                end = time.perf_counter()
                success = True

            await ctx.send(embed=discord.Embed(
                description=f'Status: {context.tick(success)} Time: `{(end - start) * 1000:.2f}ms`',
                color=formats.Colour.teal()))
        except Exception as e:
            traceback.print_exc()

    async def send_sql_results(self, ctx: Context, records: list[Any]):
        from .utils.formats import TabularData

        headers = list(records[0].keys())
        table = TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in records)
        render = table.render()

        fmt = render
        if len(fmt) > 2000:
            fp = io.BytesIO(fmt.encode('utf-8'))
            await ctx.send('Too many results...', file=discord.File(fp, 'results.sql'))
        else:
            fmt = f'```sql\n{render}\n```'
            await ctx.send(fmt)

    @_commands.command(
        commands.group,
        hidden=True,
        invoke_without_command=True,
        description='Run some SQL.'
    )
    async def sql(self, ctx: Context, *, query: str):
        """Run some SQL."""
        from .utils.formats import TabularData, plural
        import time

        query = self.cleanup_code(query)

        is_multistatement = query.count(';') > 1
        strategy: Callable[[str], Union[Awaitable[list[Record]], Awaitable[str]]]
        if is_multistatement:
            # fetch does not support multiple statements
            strategy = ctx.db.execute
        else:
            strategy = ctx.db.fetch

        try:
            start = time.perf_counter()
            results = await strategy(query)
            dt = (time.perf_counter() - start) * 1000.0
        except:  # noqa
            return await ctx.send(f'```py\n{traceback.format_exc()}\n```')

        rows = len(results)
        if isinstance(results, str) or rows == 0:
            return await ctx.send(f'`{dt:.2f}ms: {results}`')

        headers = list(results[0].keys())
        table = TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in results)
        render = table.render()

        fmt = render
        if len(fmt) > 2000:
            fp = io.BytesIO(fmt.encode('utf-8'))
            await ctx.send('Too many results...', file=discord.File(fp, 'results.sql'))
        else:
            fmt = f'```sql\n{render}\n```\n*Returned {plural(rows):row} in {dt:.2f}ms*'
            await ctx.send(fmt)

    @_commands.command(
        sql.command,
        name='schema',
        hidden=True
    )
    async def sql_schema(self, ctx: Context, *, table_name: str):
        """Runs a query describing the table schema."""
        query = """
                SELECT column_name, data_type, column_default, is_nullable
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE table_name = $1
                ORDER BY ordinal_position
            """

        results: list[Record] = await ctx.db.fetch(query, table_name)

        if len(results) == 0:
            await ctx.send('Could not find a table with that name')
            return

        await self.send_sql_results(ctx, results)

    @_commands.command(
        sql.command,
        name='tables',
        hidden=True
    )
    async def sql_tables(self, ctx: Context):
        """Lists all SQL tables in the database."""

        query = """
                SELECT table_name
                FROM INFORMATION_SCHEMA.TABLES
                WHERE table_schema='public' AND table_type='BASE TABLE'
                ORDER BY table_name;
            """

        results: list[Record] = await ctx.db.fetch(query)

        if len(results) == 0:
            await ctx.send('Could not find any tables')
            return

        await self.send_sql_results(ctx, results)

    @_commands.command(
        sql.command,
        name='sizes',
        hidden=True
    )
    async def sql_sizes(self, ctx: Context):
        """Display how much space the database is taking up."""

        query = """
                SELECT nspname || '.' || relname AS "relation",
                    pg_size_pretty(pg_relation_size(C.oid)) AS "size"
                FROM pg_class C
                LEFT JOIN pg_namespace N ON (N.oid = C.relnamespace)
                WHERE nspname NOT IN ('pg_catalog', 'information_schema')
                ORDER BY pg_relation_size(C.oid) DESC
                LIMIT 20;
            """

        results: list[Record] = await ctx.db.fetch(query)

        if len(results) == 0:
            await ctx.send('Could not find any tables')
            return

        await self.send_sql_results(ctx, results)

    @_commands.command(sql.command, name='explain', aliases=['analyze'], hidden=True)
    async def sql_explain(self, ctx: Context, *, query: str):
        """Explain an SQL query."""
        query = self.cleanup_code(query)
        analyze = ctx.invoked_with == 'analyze'
        if analyze:
            query = f'EXPLAIN (ANALYZE, COSTS, VERBOSE, BUFFERS, FORMAT JSON)\n{query}'
        else:
            query = f'EXPLAIN (COSTS, VERBOSE, FORMAT JSON)\n{query}'

        json = await ctx.db.fetchrow(query)
        if json is None:
            return await ctx.stick(False, 'Somehow nothing returned.')

        file = discord.File(io.BytesIO(json[0].encode('utf-8')), filename='explain.json')
        await ctx.send(file=file)

    @_commands.command(commands.command, hidden=True)
    async def sudo(
            self,
            ctx: Context,
            channel: Optional[discord.TextChannel],
            who: Union[discord.Member, discord.User],
            *,
            command: str,
    ):
        """Run a command as another user optionally in another channel."""
        msg = copy.copy(ctx.message)
        new_channel = channel or ctx.channel
        msg.channel = new_channel
        msg.author = who
        msg.content = ctx.prefix + command
        new_ctx = await self.bot.get_context(msg, cls=type(ctx))
        await self.bot.invoke(new_ctx)

    @_commands.command(commands.command, hidden=True)
    async def do(self, ctx: Context, times: int, *, command: str):
        """Repeats a command a specified number of times."""
        msg = copy.copy(ctx.message)
        msg.content = ctx.prefix + command

        new_ctx = await self.bot.get_context(msg, cls=type(ctx))

        for i in range(times):
            await new_ctx.reinvoke()

    @_commands.command(commands.command, hidden=True)
    async def sh(self, ctx: Context, *, command: str):
        """Runs a shell command."""
        async with ctx.typing():
            stdout, stderr = await self.run_process(command)

        if stderr:
            text = f'stdout:\n{stdout}\nstderr:\n{stderr}'
        else:
            text = stdout

        source = TextSource(prefix='```sh')
        for line in text.split('\n'):
            source.add_line(line)

        class TextPaginator(BasePaginator):

            async def format_page(self, entries: List, /) -> str:
                return entries[0]

        await TextPaginator.start(ctx, entries=source.pages, timeout=60, per_page=1)

    @_commands.command(commands.command, hidden=True)
    async def perf(self, ctx: Context, *, command: str):
        """Checks the timing of a command, attempting to suppress HTTP and DB calls."""

        msg = copy.copy(ctx.message)
        msg.content = ctx.prefix + command

        new_ctx = await self.bot.get_context(msg, cls=type(ctx))

        new_ctx._state = PerformanceMocker()  # type: ignore
        new_ctx.channel = PerformanceMocker()  # type: ignore

        if new_ctx.command is None:
            return await ctx.send('No command found')

        start = time.perf_counter()
        try:
            await new_ctx.command.invoke(new_ctx)
        except commands.CommandError:
            end = time.perf_counter()
            success = False
            try:
                await ctx.send(f'```py\n{traceback.format_exc()}\n```')
            except discord.HTTPException:
                pass
        else:
            end = time.perf_counter()
            success = True

        await ctx.send(f'Status: {context.tick(success)} Time: `{(end - start) * 1000:.2f}ms`')


async def setup(bot: RoboHashira):
    await bot.add_cog(Admin(bot))
