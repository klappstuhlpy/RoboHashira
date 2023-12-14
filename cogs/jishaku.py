import importlib
import sys
import traceback
import typing
from importlib.metadata import distribution, packages_distributions

import discord
import jishaku
import psutil
from discord.ext import commands
from jishaku.cog import OPTIONAL_FEATURES, STANDARD_FEATURES
from jishaku.features.baseclass import Feature
from jishaku.math import natural_size
from jishaku.modules import package_version

jishaku.Flags.NO_DM_TRACEBACK = True
jishaku.Flags.NO_UNDERSCORE = True
jishaku.Flags.HIDE = True


class Jishaku(*OPTIONAL_FEATURES, *STANDARD_FEATURES):

    @Feature.Command(
        parent="jsk",
        name="mrl",
        invoke_without_commad=True,
        ignore_extra=False,
        hidden=True,
    )
    async def reload_module(self, ctx: commands.Context, *, module: str):
        """Reloads a module."""

        module = sys.modules.get(module, None)
        icon = "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}"

        if not module:
            return await ctx.send(f"{icon}\N{WARNING SIGN} ``{module}`` was not loaded.")

        try:
            importlib.reload(module)
        except:
            return await ctx.send(f"{icon}\N{WARNING SIGN} ``{module}`` was not reloaded.\n"
                                  f"```py\n{traceback.format_exc()}\n```")
        await ctx.send(f"{icon} ``{module}`` was reloaded successfully.")

    @Feature.Command(
        parent="jsk",
        name="ml",
        invoke_without_commad=True,
        ignore_extra=False,
        hidden=True,
    )
    async def load_module(self, ctx: commands.Context, *, module: str):
        """Reloads a module."""

        icon = "\N{INBOX TRAY}"
        try:
            importlib.import_module(module)
        except:
            return await ctx.send(f"{icon}\N{WARNING SIGN} ``{module}`` was not loaded.\n"
                                  f"```py\n{traceback.format_exc()}\n```")
        await ctx.send(f"{icon} ``{module}`` was loaded successfully.")

    @Feature.Command(
        parent="jsk",
        name="mul",
        invoke_without_commad=True,
        ignore_extra=False,
        hidden=True,
    )
    async def unload_module(self, ctx: commands.Context, *, module: str):
        """Reloads a module."""

        icon = "\N{OUTBOX TRAY}"

        try:
            del sys.modules[module]
        except Exception as exc:
            match exc:
                case KeyError():
                    return await ctx.send(f"{icon}\N{WARNING SIGN} ``{module}`` was not loaded.")
                case _:
                    return await ctx.send(f"{icon}\N{WARNING SIGN} ``{module}`` was not unloaded.\n"
                                          f"```py\n{traceback.format_exc()}\n```")
        await ctx.send(f"{icon} ``{module}`` was unloaded successfully.")

    @Feature.Command(
        name="jishaku",
        aliases=["jsk"],
        invoke_without_command=True,
        ignore_extra=False,
        hidden=True,
    )
    async def jsk(self, ctx: commands.Context):
        """
        The Jishaku debug and diagnostic commands.
        This command on its own gives a status brief.
        All other functionality is within its subcommands.
        """

        distributions: typing.List[str] = [
            dist
            for dist in packages_distributions()["discord"]
            if any(
                file.parts == ("discord", "__init__.py")
                for file in distribution(dist).files
            )
        ]

        if distributions:
            dist_version = f"{distributions[0]} `{package_version(distributions[0])}`"
        else:
            dist_version = f"unknown `{discord.__version__}`"

        summary = [
            f"Jishaku `v{package_version('jishaku')}`, {dist_version}, "
            f"Python `{sys.version}` on `{sys.platform}`".replace("\n", ""),
            f"Module was loaded <t:{self.load_time.timestamp():.0f}:R>, "
            f"cog was loaded <t:{self.start_time.timestamp():.0f}:R>.",
            "",
        ]

        if psutil:
            try:
                proc = psutil.Process()

                with proc.oneshot():
                    try:
                        mem = proc.memory_full_info()
                        summary.append(
                            f"Using `{natural_size(mem.rss)}` physical memory and "
                            f"`{natural_size(mem.vms)}` virtual memory, "
                            f"`{natural_size(mem.uss)}` of which unique to this process."
                        )
                    except psutil.AccessDenied:
                        pass

                    try:
                        name = proc.name()
                        pid = proc.pid
                        thread_count = proc.num_threads()

                        summary.append(
                            f"Running on PID `{pid}` (`{name}`) with `{thread_count}` thread(s)."
                        )
                    except psutil.AccessDenied:
                        pass

                    summary.append("")
            except psutil.AccessDenied:
                summary.append(
                    "psutil is installed, but this process does not have high enough access rights "
                    "to query process information."
                )
                summary.append("")  # blank line
        s_for_guilds = "" if len(self.bot.guilds) == 1 else "s"
        s_for_users = "" if len(self.bot.users) == 1 else "s"
        cache_summary = f"`{len(self.bot.guilds)}` guild{s_for_guilds} and `{len(self.bot.users)}` user{s_for_users}"

        if isinstance(self.bot, discord.AutoShardedClient):
            if len(self.bot.shards) > 20:
                summary.append(
                    f"This bot is automatically sharded (`{len(self.bot.shards)}` shards of `{self.bot.shard_count}`)"
                    f" and can see {cache_summary}."
                )
            else:
                shard_ids = ", ".join(str(i) for i in self.bot.shards.keys())
                summary.append(
                    f"This bot is automatically sharded (Shards `{shard_ids}` of `{self.bot.shard_count}`)"
                    f" and can see {cache_summary}."
                )
        elif self.bot.shard_count:
            summary.append(
                f"This bot is manually sharded (Shard `{self.bot.shard_id}` of `{self.bot.shard_count}`)"
                f" and can see {cache_summary}."
            )
        else:
            summary.append(f"This bot is not sharded and can see {cache_summary}.")

        if self.bot._connection.max_messages:  # type: ignore
            message_cache = f"Message cache capped at `{self.bot._connection.max_messages}`"
        else:
            message_cache = "Message cache is disabled"

        remarks = {True: "enabled", False: "disabled", None: "unknown"}

        *group, last = (
            f"{intent.replace('_', ' ')} intent is {remarks.get(getattr(self.bot.intents, intent, None))}"
            for intent in ("presences", "members", "message_content")
        )

        summary.append(f"{message_cache}, {', '.join(group)}, and {last}.")

        summary.append(
            f"Average websocket latency: `{round(self.bot.latency * 1000, 2)} ms`"
        )

        embed = discord.Embed(description="\n".join(summary), color=formats.Colour.teal())
        embed.set_author(name=ctx.bot.user.name, icon_url=ctx.bot.user.avatar.url)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Jishaku(bot=bot))
