from typing import Literal, cast, TYPE_CHECKING

import discord
import wavelink
from discord import app_commands

from bot import RoboHashira
from .utils.context import Context
from .utils import checks, helpers, commands
from cogs.utils.player import Player

if TYPE_CHECKING:
    from .music import Music


class Radio(commands.Cog):
    """Radio Stations for your Server!
    Play 24/7 Radio Stations from YouTube."""

    def __init__(self, bot: RoboHashira):
        self.bot: RoboHashira = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='\N{RADIO}')

    @commands.command(
        description='Adds a track/playlist to the queue and play the next available track.',
        guild_only=True
    )
    @app_commands.describe(source='The Radio Station you want to play from.')
    @checks.is_author_connected()
    @checks.is_listen_together()
    async def radio(self, ctx: Context, source: Literal['Antenne 1 (Germany)', 'I Love Radio', 'JoyHits']):
        """Plays a Radio Station from YouTube."""
        await ctx.defer()

        player: Player = cast(Player, ctx.voice_client)

        if not player:
            music: Music = self.bot.get_cog('Music')  # type: ignore
            player = await music.join(ctx)

        source_urls = {
            'Antenne 1 (Germany)': 'http://stream.antenne1.de/a1stg/livestream2.mp3',
            'JoyHits': 'http://joyhits.online/joyhits.flac.ogg',
            'I Love Radio': 'http://stream01.iloveradio.de/iloveradio1.mp3',
        }

        result = await wavelink.Pool.fetch_tracks(source_urls.get(source))

        if not result:
            return await ctx.send('Sorry, seems like something went wrong!', ephemeral=True, delete_after=10)

        message_prefix = '`📻`'
        if player.playing:
            message_prefix += ' Switched to'

        if player.playing:
            player.reset_queue()
            await player.stop()

        setattr(result, 'requester', ctx.author)
        await player.queue.put_wait(result)
        await player.play(player.queue.get(), volume=70)

        embed = discord.Embed(
            title=source,
            description=f'{message_prefix} Radio Station: **{source}**',
            colour=helpers.Colour.teal())
        await ctx.send(embed=embed, delete_after=15)


async def setup(bot: RoboHashira):
    await bot.add_cog(Radio(bot))
