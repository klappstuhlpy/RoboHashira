from __future__ import annotations
import asyncio
import logging
import random
from contextlib import suppress
from typing import Literal, Optional, Union, List, cast, TYPE_CHECKING, Annotated
import datetime
from urllib.parse import urljoin

import discord
import wavelink
from bs4 import BeautifulSoup, Tag, PageElement, NavigableString
from discord import app_commands

from discord.utils import MISSING
from wavelink import DiscordVoiceCloseType

from launcher import get_logger
from .utils import checks, converters, helpers, commands
from .utils.constants import VOLUME_REGEX
from .utils.context import Context, tick
from .utils.queue import ShuffleMode
from .utils.render import Render
from .utils.paginator import BasePaginator
from .utils.player import Player, PlayerPanel, SearchReturn
from bot import RoboHashira

if TYPE_CHECKING:
    from .playlist import PlaylistTools

log = get_logger(__name__)


class PlayFlags(commands.FlagConverter, prefix='--', delimiter=' '):
    """Flags for the music commands."""
    query: str = commands.Flag(name='query', aliases=['q'])
    query.__setattr__('without_prefix', True)

    source: Literal['yt', 'sp', 'sc'] = commands.Flag(name='source', aliases=['s'], default='yt')
    force: Optional[bool] = commands.Flag(name='force', aliases=['f'], default=False)


class VolumeConverter(commands.Converter):
    async def convert(self, ctx: Context, argument: str) -> int:
        player: Player = cast(Player, ctx.voice_client)

        if not (match := VOLUME_REGEX.match(argument)):
            raise commands.BadArgument(
                'Invalid Volume provided.\n'
                'Please provide a valid number between **0-100** or a relative number, e.g. **+10** or **-15**.')

        if match.group().startswith(('+', '-')):
            return player.volume + int(match.group()[1:])
        return int(match.group())


class Music(commands.Cog):
    """Commands for playing music in a voice channel."""

    def __init__(self, bot: RoboHashira):
        self.bot: RoboHashira = bot
        self.render = Render

    async def cog_check(self, ctx: Context) -> bool:
        if not ctx.guild:
            return False
        return True

    async def cog_before_invoke(self, ctx: Context) -> None:
        playlist_tools: PlaylistTools = self.bot.get_cog('PlaylistTools')  # type: ignore
        await playlist_tools.initizalize_user(ctx.author)

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='music', id=1080849654637404280)

    @commands.Cog.listener(name='on_wavelink_track_exception')
    @commands.Cog.listener(name='on_wavelink_track_stuck')
    @commands.Cog.listener(name='on_wavelink_websocket_closed')
    @commands.Cog.listener(name='on_wavelink_extra_event')
    async def on_wavelink_intercourse(
            self,
            payload: Union[
                wavelink.TrackExceptionEventPayload,
                wavelink.TrackStuckEventPayload,
                wavelink.WebsocketClosedEventPayload,
                wavelink.ExtraEventPayload]
    ):
        # Handles all wavelink errors
        if isinstance(payload, wavelink.WebsocketClosedEventPayload):
            if payload.code in (
                    DiscordVoiceCloseType.DISCONNECTED, DiscordVoiceCloseType.CLOSE_NORMAL,
                    DiscordVoiceCloseType.SESSION_INVALID
            ):
                return

        player: Player | None = cast(Player, payload.player)

        if player:
            try:
                await player.disconnect()
            except Exception as exc:
                log.debug(f'Error while destroying player: {exc}')
                pass

        args = ['%s=%r' % (k, v) for k, v in vars(payload).items()]
        log.warning(f'Wavelink Error Occured: {payload.__class__.__name__} | {', '.join(args)}')

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        logging.info(f'Wavelink Node connected: {payload.node.uri} | Resumed: {payload.resumed}')

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: Player) -> None:
        if not player:
            return

        with suppress(discord.HTTPException):
            await player.channel.send(
                f'The player has been inactive for `{player.inactive_timeout}` seconds. *Goodbye!*')

        if player.connected:
            await player.disconnect()

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player: Player | None = cast(Player, payload.player)
        if not player:
            return

        if player.queue.listen_together is not MISSING:
            member = await self.bot.get_or_fetch_member(
                player.guild, player.queue.listen_together)
            if (activity := next((a for a in member.activities if isinstance(a, discord.Spotify)), None)) is None:
                return await player.disconnect()

            try:
                track = await player.search(activity.track_url)
            except Exception as exc:
                log.debug(f'Error while searching for track: {exc}')
                return await player.panel.channel.send('I couldn\'t find the track you were listening to on Spotify.')

            player.queue.reset()
            await player.queue.put_wait(track)
            await player.play(player.queue.get())
            return await player.send_track_add(track)

        # This is a custom shuffle to preserve
        # insert order of the tracks to the queue
        # This only plays random tracks by indexing tracks
        # with random numbers in the queue.

        # This makes it possible for the user to turn of shuffle and still have
        # the original insert order of tracks in the queue.
        if player.queue.shuffle is ShuffleMode.on:
            queue = player.queue.all
            next_random_track = queue[random.randint(0, len(queue) - 1)]

            # Add all tracks that are before the next random track to the history
            player.queue.history.clear()
            player.queue.history.put(queue[:queue.index(next_random_track)])

            # Add all tracks that are after the next random track to the queue
            player.queue.clear()
            await player.queue.put_wait(queue[queue.index(next_random_track):])

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player: Player | None = cast(Player, payload.player)

        if not player:
            # Shouldn't happen, would likely be a connection error/downtime of the bot
            return

        if player.current.recommended:
            player.queue.history.put(player.current)

        while not player.queue.all or player.current not in player.queue.all:
            await asyncio.sleep(0.5)

        await player.panel.update()

    @staticmethod
    def _get_spotify_activity(member: discord.Member) -> Optional[discord.Spotify]:
        return next((a for a in member.activities if isinstance(a, discord.Spotify)), None)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        await self.bot.wait_until_ready()
        player: Player | None = cast(Player, before.guild.voice_client)

        if not player:
            return

        user_id = player.queue.listen_together
        if user_id is MISSING:
            return

        before_activity = self._get_spotify_activity(before)
        after_activity = self._get_spotify_activity(after)

        if before.id != user_id:
            return

        if before_activity and after_activity:
            if before_activity.title == after_activity.title:
                now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
                start = after_activity.start.replace(tzinfo=None)
                end = after_activity.end.replace(tzinfo=None)

                deter = (end - now).total_seconds() if now > end else (now - start).total_seconds()
                position = round(deter) * 1000

                await player.seek(position)
                await player.panel.update()
            else:
                new_activity = self._get_spotify_activity(after)
                if new_activity and new_activity.title == before_activity.title:
                    await player.pause(False)
                else:
                    player.queue.reset()

                    try:
                        track = await player.search(new_activity.track_url)
                    except Exception as exc:
                        log.debug(f'Error while searching for track: {exc}')
                        return await player.panel.channel.send(
                            f'{tick(False)} I couldn\'t find the track <@{user_id}> was listening to on spotify.',
                            delete_after=10)

                    await player.queue.put_wait(track)
                    await player.send_track_add(track)
                    await player.play(player.queue.get())

                    position = round(
                        (datetime.datetime.now(datetime.UTC) - new_activity.start.replace(
                            tzinfo=None)).total_seconds()) * 1000
                    await player.seek(position)
        else:
            await player.panel.channel.send('The host has stopped listening to Spotify.')
            await player.disconnect()

    async def join(self, obj: discord.Interaction | Context) -> Player:
        channel = obj.user.voice.channel if obj.user.voice else None  # type: ignore
        if not channel:
            func = app_commands.AppCommandError if isinstance(obj, discord.Interaction) else commands.BadArgument
            func('You need to be in a voice channel or provide one to connect to.')

        player = await channel.connect(cls=Player(self.bot), self_deaf=True)

        if isinstance(channel, discord.StageChannel):
            if not channel.instance:
                await channel.create_instance(topic=f'Music by {obj.guild.me.display_name}')
            await obj.guild.me.edit(suppress=False)

        player.panel = await PlayerPanel.start(player, channel=obj.channel)
        return player

    @commands.command(
        description='Adds a track/playlist to the queue and play the next available track.',
        guild_only=True
    )
    @app_commands.describe(
        query='The track/playlist to add to the queue.',
        source='The type of search to perform.',
        force='Force the track to be added to the queue.')
    @app_commands.choices(
        source=[
            app_commands.Choice(name='YouTube (Default)', value='yt'),
            app_commands.Choice(name='Spotify', value='sp'),
            app_commands.Choice(name='SoundCloud', value='sc')]
    )
    @checks.is_author_connected()
    @checks.is_listen_together()
    async def play(self, ctx: Context, *, flags: PlayFlags):
        """Play Music in a voice channel by searching for a track/playlist or by providing a file.
        **You can play from sources such as YouTube, Spotify, SoundCloud, and more.**
        `Note:` There is an automatic play function that will play the next available track in the queue.
        This command uses a syntax similar to Discord's search bar.
        The following options are valid.
        `query:` The query you want to search for. Could be a URL or a keyword.
        `source:` The Streaming Source you want to search for. Defaults to YouTube.
        `force:` Whether to force the track to be added to the front of the queue.
        """
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            player = await self.join(ctx)

        sources = {
            'yt': wavelink.TrackSource.YouTubeMusic,
            'sp': 'spsearch',
            'sc': wavelink.TrackSource.SoundCloud
        }
        flags.source = sources.get(flags.source, wavelink.TrackSource.YouTubeMusic)

        player.autoplay = wavelink.AutoPlayMode.enabled

        if not flags.query:
            return await ctx.stick(False, 'Please provide a search query.', ephemeral=True,
                                   delete_after=10)

        result = await player.search(flags.query, source=flags.source, ctx=ctx)

        if isinstance(result, SearchReturn):
            if result == SearchReturn.NO_RESULTS:
                await ctx.stick(False, 'Sorry! No results found matching your query.',
                                       ephemeral=True, delete_after=10)
            return

        if await player.check_blacklist(result, pool=self.bot.pool):
            return await ctx.stick(False, 'Blacklisted Track detected. Please try another one.',
                                   ephemeral=True, delete_after=10)

        if isinstance(result, wavelink.Playlist):
            before_count = len(player.queue.all)

            result.track_extras(requester=ctx.author)
            added: int = await player.queue.put_wait(result)

            embed = discord.Embed(title='Playlist Enqueued',
                                  description=f'`🎶` Enqueued successfully **{added}** tracks from [{result.name}]({result.url}).\n'
                                              f'`🎵` *Next Track at Position **#{before_count + 1}/{len(player.queue.all)}***',
                                  color=helpers.Colour.teal())
            if result.artwork:
                embed.set_thumbnail(url=result.artwork)

            embed.set_footer(text=f'Requested by: {ctx.author}', icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed, delete_after=15)
        else:
            setattr(result, 'requester', ctx.author)
            if flags.force:
                player.queue.put_at(0, result)
            else:
                await player.queue.put_wait(result)

            await player.send_track_add(result, ctx)

        if player.playing and flags.force:
            await player.skip()
        elif not player.playing:
            await player.play(player.queue.get(), volume=70)
        else:
            await player.panel.update()

    listen_together = app_commands.Group(
        name='listen-together', description='Listen-together related commands.')

    @commands.command(
        listen_together.command,
        name='start',
        description='Start a listen-together activity with a user.',
        guild_only=True
    )
    @app_commands.describe(member='The user you want to start a listen-together activity with.')
    @checks.is_author_connected()
    async def listen_together_start(self, interaction: discord.Interaction, member: discord.Member):
        """Start a listen-together activity with an user.
        `Note:` Only supported for Spotify Music."""
        if not interaction.guild.voice_client:
            await self.join(interaction)

        player: Player = cast(Player, interaction.guild.voice_client)
        if not player:
            return

        # We need to fetch the member to get the current activity
        member = await self.bot.get_or_fetch_member(interaction.guild, member.id)

        if not (activity := next((a for a in member.activities if isinstance(a, discord.Spotify)), None)):
            return await interaction.response.send_message(
                f'{tick(False)} {member} isn\'t listening to anything right now.', ephemeral=True,
                delete_after=10)

        if player.playing or player.queue.listen_together is not MISSING:
            player.queue.reset()
            await player.stop()

        player.autoplay = wavelink.AutoPlayMode.disabled

        try:
            track = await player.search(activity.track_url)
        except Exception as exc:
            log.debug(f'Error while searching for track: {exc}')
            return await interaction.response.send_message(
                f'{tick(False)} The User isn\'t playing anything right now.', ephemeral=True,
                delete_after=10)

        track.track_extras(requester=interaction.user)
        await player.queue.put_wait(track)
        player.queue.listen_together = member.id
        await player.play(player.queue.get())

        poss = round(
            (datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - activity.start.replace(tzinfo=None)
             ).total_seconds()) * 1000
        await player.seek(poss)

        await player.send_track_add(track, interaction)
        await player.panel.update()

    @commands.command(
        listen_together.command,
        name='stop',
        description='Stops the current listen-together activity.',
        guild_only=True
    )
    async def listen_together_stop(self, interaction: discord.Interaction):
        """Stops the current listen-together activity."""
        player: Player = cast(Player, interaction.guild.voice_client)
        if not player:
            return

        if player.queue.listen_together is MISSING:
            return await interaction.response.send_message(
                f'{tick(False)} There is currently no listen-together activity started.',
                ephemeral=True, delete_after=10)

        await player.disconnect()
        await interaction.response.send_message(
            f'{tick(True)} Stopped the current listen-together activity.', delete_after=10)

    @commands.command(name='connect', description='Connect me to a voice-channel.', guild_only=True)
    @app_commands.describe(channel='The Voice/Stage-Channel you want to connect to.')
    async def connect(self, ctx: Context, channel: Union[discord.VoiceChannel, discord.StageChannel] = None):
        """Connect me to a voice-channel."""
        if ctx.voice_client:
            return await ctx.stick(
                False, 'I am already connected to a voice channel. Please disconnect me first.')

        try:
            channel = channel or ctx.author.voice.channel
        except AttributeError:
            return await ctx.stick(
                False, 'No voice channel to connect to. Please either provide one or join one.')

        await self.join(ctx)
        await ctx.stick(True, f'Connected and bound to {channel.mention}', delete_after=10)

    @commands.command(description='Disconnect me from a voice-channel.', guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_connected()
    async def leave(self, ctx: Context):
        """Disconnect me from a voice-channel."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await player.disconnect()
        await ctx.stick(True, 'Disconnected Channel and cleaned up the queue.', delete_after=10)

    @commands.command(name='stop', description='Clears the queue and stop the current plugins.', guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def stop(self, ctx: Context):
        """Clears the queue and stop the current plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await player.disconnect()
        await ctx.stick(True, 'Stopped Track and cleaned up queue.', delete_after=10)

    @commands.command(
        name='toggle',
        aliases=['pause', 'resume'],
        description='Pause/Resume the current track.',
        guild_only=True
    )
    @checks.is_author_connected()
    @checks.is_listen_together()
    async def pause_or_resume(self, ctx: Context):
        """Pause the current playing track."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await player.pause(not player.paused)
        await ctx.stick(
            True, f'{'Paused' if player.paused else 'Resumed'} Track [{player.current.title}]({player.current.uri})',
            delete_after=10, suppress_embeds=True)
        await player.panel.update()

    @commands.command(description='Sets a loop mode for the plugins.', guild_only=True)
    @app_commands.describe(mode='Select a loop mode.')
    @app_commands.choices(
        mode=[
            app_commands.Choice(name='Normal', value='normal'),
            app_commands.Choice(name='Track', value='track'),
            app_commands.Choice(name='Queue', value='queue')
        ]
    )
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def loop(self, ctx: Context, mode: Literal['normal', 'track', 'queue']):
        """Sets a loop mode for the plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        player.queue.mode = {'normal': 0, 'track': 1, 'queue': 2}.get(mode)

        await player.panel.update()
        await ctx.stick(True, f'Loop Mode changed to `{mode}`', delete_after=10)

    @commands.command(description='Sets the shuffle mode for the plugins.', guild_only=True)
    @app_commands.describe(mode='Select a shuffle mode.')
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def shuffle(self, ctx: Context, mode: bool):
        """Sets the shuffle mode for the plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        player.queue.shuffle = ShuffleMode.on if mode else ShuffleMode.off
        await player.panel.update()
        await ctx.stick(True, f'Shuffle Mode changed to `{mode}`', delete_after=10)

    @commands.command(description='Seek to a specific position in the tack.', guild_only=True)
    @app_commands.describe(position='The position to seek to. (Format: HH:MM:SS)')
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def seek(self, ctx: Context, position: Optional[str] = None):
        """Seek to a specific position in the tack."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if player.current.is_stream:
            return await ctx.stick(False, 'Cannot seek if track is a stream.', ephemeral=True, delete_after=10)

        if position is None:
            seconds = 0
            await player.seek(seconds)
        else:
            try:
                seconds = sum(int(x) * 60 ** i for i, x in enumerate(reversed(position.split(':'))))
            except ValueError:
                return await ctx.stick(False, 'Please provide a valid timestamp format. (e.g. 3:20)', ephemeral=True)

            seconds = int(seconds) * 1000
            if seconds in range(int(player.current.length)):
                await player.seek(seconds)
            else:
                return await ctx.stick(
                    False, 'Please provide a seek time within the range of the track.',
                    ephemeral=True, delete_after=10)

        await ctx.stick(
            True, f'Seeked to position ``{converters.convert_duration(seconds)}``', delete_after=10)
        await player.panel.update()

    @seek.autocomplete('position')
    async def seek_autocomplete(self, ctx: Context, current: str) -> list[app_commands.Choice[str]]:
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return []

        try:
            seconds = sum(
                int(x.strip('""')) * 60 ** inT for inT, x in enumerate(reversed(current.split(':'))))
        except ValueError or TypeError:
            return []

        return [
            app_commands.Choice(
                name=datetime.datetime.fromtimestamp(int(seconds), datetime.UTC).strftime('%H:%M:%S'),
                value=datetime.datetime.fromtimestamp(int(seconds), datetime.UTC).strftime('%H:%M:%S'))
        ]

    @commands.command(description='Set the volume for the plugins.', guild_only=True)
    @app_commands.describe(amount='The volume to set the plugins to. (0-100)')
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def volume(self, ctx: Context, amount: Optional[Annotated[int, VolumeConverter]] = None):
        """Set the volume for the plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if amount is None:
            embed = discord.Embed(title=f'Current Volume', color=helpers.Colour.teal())
            embed.add_field(
                name=f'Volume:',
                value=f'```swift\n{converters.VisualStamp(0, 100, player.volume)} [ {player.volume}% ]```',
                inline=False)
            return await ctx.send(embed=embed, delete_after=10)

        await player.set_volume(amount)
        await player.panel.update()

        embed = discord.Embed(title=f'Changed Volume', color=helpers.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        embed.add_field(
            name=f'Volume:',
            value=f'```swift\n{converters.VisualStamp(0, 100, player.volume)} [ {player.volume}% ]```',
            inline=False)
        await ctx.send(embed=embed, delete_after=10)

    @commands.command(description='Removes all songs from users that are not in the voice channel.', guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def cleanupleft(self, ctx: Context):
        """Removes all songs from users that are not in the voice channel."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await player.cleanupleft()
        await player.panel.update()
        await ctx.stick(True, 'Cleaned up the queue.', delete_after=10)

    @commands.command(
        commands.hybrid_group,
        description='Manage Advanced Filters to specify you listening experience.',
        guild_only=True
    )
    @commands.guild_only()
    async def filter(self, ctx: Context):
        """Find useful information about the filter command group."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @commands.command(
        filter.command,
        name='equalizer',
        description='Set the equalizer for the current Track.'
    )
    @app_commands.describe(
        band='The Band you want to change. (1-15)',
        gain='The Gain you want to set. (-0.25-+1.0)'
    )
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_equalizer(
            self,
            ctx: Context,
            band: app_commands.Range[int, 1, 15] = None,
            gain: app_commands.Range[float, -0.25, +1.0] = None
    ):
        """Set a custom Equalizer for the current Track.

        Note:
        The preset paremeter will be given priority, if provided.
        """
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if ctx.interaction:
            await ctx.defer()
        else:
            await ctx.channel.typing()

        filters = player.filters
        if not band or not gain:
            return await ctx.stick(False, 'Please provide a valid Band and Gain or a Preset.')

        band -= 1

        eq = filters.equalizer.payload
        eq[band]['gain'] = gain
        filters.equalizer.set(bands=[dicT for dicT in eq.values()])
        await player.set_filters(filters)

        embed = discord.Embed(title=f'Changed Filter', color=helpers.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        file = discord.File(
            fp=self.render.generate_eq_image([entry['gain'] for entry in filters.equalizer.payload.values()]),
            filename='image.png')
        embed.set_image(url='attachment://image.png')
        embed.set_footer(text=f'Requested by: {ctx.author}')
        await ctx.send(embed=embed, file=file, delete_after=20)

    @commands.command(filter.command, name='bassboost', description='Enable/Disable the bassboost filter.')
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_bassboost(self, ctx: Context):
        """Apply a bassboost filter for the current track."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if ctx.interaction:
            await ctx.defer()
        else:
            await ctx.channel.typing()

        filters = player.filters
        filters.equalizer.set(bands=[
            {'band': 0, 'gain': 0.2}, {'band': 1, 'gain': 0.15}, {'band': 2, 'gain': 0.1},
            {'band': 3, 'gain': 0.05}, {'band': 4, 'gain': 0.0}, {'band': 5, 'gain': -0.05},
            {'band': 6, 'gain': -0.1}, {'band': 7, 'gain': -0.1}, {'band': 8, 'gain': -0.1},
            {'band': 9, 'gain': -0.1}, {'band': 10, 'gain': -0.1}, {'band': 11, 'gain': -0.1},
            {'band': 12, 'gain': -0.1}, {'band': 13, 'gain': -0.1}, {'band': 14, 'gain': -0.1}
        ])
        await player.set_filters(filters)

        embed = discord.Embed(title=f'Changed Filter', color=helpers.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        file = discord.File(
            fp=self.render.generate_eq_image([entry['gain'] for entry in filters.equalizer.payload.values()]),
            filename='image.png')
        embed.set_image(url='attachment://image.png')
        embed.set_footer(text=f'Requested by: {ctx.author}')
        await ctx.send(embed=embed, file=file, delete_after=20)

    @commands.command(filter.command, name='nightcore', description='Enables/Disables the nightcore filter.')
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_nightcore(self, ctx: Context):
        """Apply a Nightcore Filter to the current track."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        filters = player.filters
        filters.timescale.set(speed=1.25, pitch=1.3, rate=1.3)
        await player.set_filters(filters)

        embed = discord.Embed(title=f'Changed Filter', color=helpers.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        await ctx.send(embed=embed, delete_after=10)

    @commands.command(filter.command, name='8d', description='Enable/Disable the 8d filter.')
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_8d(self, ctx: Context):
        """Apply an 8D Filter to create a 3D effect."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        filters = player.filters
        filters.rotation.set(rotation_hz=0.15)
        await player.set_filters(filters)

        embed = discord.Embed(title=f'Changed Filter', color=helpers.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        await ctx.send(embed=embed, delete_after=10)

    @commands.command(
        filter.command,
        name='lowpass',
        description='Suppresses higher frequencies while allowing lower frequencies to pass through.'
    )
    @app_commands.describe(smoothing='The smoothing of the lowpass filter. (2.5-50.0)')
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_lowpass(self, ctx: Context, smoothing: app_commands.Range[float, 2.5, 50.0]):
        """Apply a Lowpass Filter to the current Track."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        filters = player.filters
        filters.low_pass.set(smoothing=smoothing)
        await player.set_filters(filters)

        embed = discord.Embed(title=f'Changed Filter', color=helpers.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        embed.add_field(name=f'Applied LowPass Filter:',
                        value=f'Set Smoothing to ``{smoothing}``.',
                        inline=False)
        await ctx.send(embed=embed, delete_after=10)

    @commands.command(filter.command, name='reset', description='Reset all active filters.')
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_reset(self, ctx: Context):
        """Reset all active filters."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        player.filters.reset()
        await player.set_filters()
        await ctx.stick(True, 'Removed all active filters.', delete_after=10)

    @commands.command(description='Skip the playing song to the next.', guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def forceskip(self, ctx: Context):
        """Skip the playing song."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if player.queue.is_empty:
            return await ctx.stick(False, 'The queue is empty.', ephemeral=True, delete_after=10)

        await player.skip(force=True)
        await ctx.stick(True, 'An admin or DJ has to the next track.', delete_after=10)

    @commands.command(name='jump-to', description='Jump to a track in the Queue.', guild_only=True)
    @app_commands.describe(position='The index of the track you want to jump to.')
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def jump_to(self, ctx: Context, position: int):
        """Jump to a track in the Queue.
        Note: The number you enter is the count of how many tracks in the queue will be skipped."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if player.queue.all_is_empty:
            return await ctx.stick(False, 'The queue is empty.', ephemeral=True, delete_after=10)

        if position < 0:
            return await ctx.stick(
                False, 'The index must be greater than or 0.', ephemeral=True, delete_after=10)

        if (position - 1) > len(player.queue.all):
            return await ctx.stick(
                False, 'There are not that many tracks in the queue.', ephemeral=True, delete_after=10)

        success = await player.jump_to(position - 1)
        if not success:
            return await ctx.stick(
                False, 'Failed to jump to the specified track.', ephemeral=True, delete_after=10)

        await player.stop()

        if position != 1:
            await ctx.stick(True, f'Playing the **{position}** track in queue.', delete_after=10)
        else:
            await ctx.stick(True, 'Playing the next track in queue.', delete_after=10)

    @commands.command(description='Plays the previous Track.', guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def back(self, ctx: Context):
        """Plays the previous Track."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if player.queue.history.is_empty:
            return await ctx.stick(
                False, 'There are no tracks in the history.', ephemeral=True, delete_after=10)

        await player.back()
        await ctx.stick(True, 'An admin or DJ has skipped to the previous song.', delete_after=10)

    @commands.command(description='Display the active queue.', guild_only=True)
    async def queue(self, ctx: Context):
        """Display the active queue."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if player.queue.all_is_empty:
            return await ctx.stick(False, 'No items currently in the queue.', ephemeral=True)

        await ctx.defer()

        class QueuePaginator(BasePaginator):
            @staticmethod
            def fmt(track: wavelink.Playable, index: int) -> str:
                return (
                    f'`[ {index}. ]` [{track.title}]({track.uri}) by **{track.author or 'Unknown'}** '
                    f'[`{converters.convert_duration(track.length)}`]'
                )

            async def format_page(self, entries: List, /) -> discord.Embed:
                embed = discord.Embed(color=helpers.Colour.teal())
                embed.set_author(name=f'{ctx.guild.name}\'s Current Queue', icon_url=ctx.guild.icon.url)

                embed.description = (
                    '**╔ Now Playing:**\n'
                    f'[{player.current.title}]({player.current.uri}) by **{player.current.author or 'Unknown'}** '
                    f'[`{converters.convert_duration(player.current.length)}`]\n\n'
                )

                tracks = (
                    '\n'.join(self.fmt(track, i) for i, track in enumerate(entries, (self._current_page * self.per_page) + 1))
                ) if not isinstance(entries[0], str) else (
                    '*It seems like there are currently not upcomming tracks.*\n'
                    'Add one with </play:1079059790380142762>.'
                )

                embed.description += '**╠ Up Next:**\n' + tracks

                embed.add_field(name='╚ Settings:', value=f'DJ: {player.dj.mention}', inline=False)
                embed.set_footer(text=f'Total: {len(player.queue.all)} • History: {len(player.queue.history) - 1}')
                return embed

        await QueuePaginator.start(ctx, entries=list(player.queue) or ['PLACEHOLDER'], per_page=30)

    # Lyrics Stuff

    @classmethod
    def _get_text(cls, element: Tag | PageElement) -> str:
        """Recursively parse an element and its children into a markdown string."""
        if isinstance(element, NavigableString):
            return element.strip()
        elif element.name == 'br':
            return '\n'
        else:
            return ''.join(cls._get_text(child) for child in element.contents)

    @classmethod
    def _extract_lyrics(cls, html: str) -> Optional[str]:
        """Extract lyrics from the provided HTML."""
        soup = BeautifulSoup(html, 'html.parser')

        lyrics_container = soup.find_all('div', {'data-lyrics-container': 'true'})

        if not lyrics_container:
            return None

        text_parts = []
        for part in lyrics_container:
            text_parts.append(cls._get_text(part))

        return '\n'.join(text_parts)

    @commands.command(description='Search for some lyrics.')
    @app_commands.describe(song='The song you want to search for.')
    @commands.guild_only()
    async def lyrics(self, ctx: Context, *, song: str = None):
        """Search for some lyrics."""
        await ctx.defer(ephemeral=True)
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            if not song:
                await ctx.stick(False, 'Please provide a song to search for.', ephemeral=True,
                                delete_after=10)
                return

        song = song or f'{player.current.title} by {player.current.author}'
        mess = await ctx.send(content=f'\🔎 *Searching lyrics for {song}...*', ephemeral=True)

        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.bot.config.genius.access_token}'
        }

        async with self.bot.session.get(
                'https://api.genius.com/search', headers=headers, params={
                    'q': song.replace('by', '').replace('from', '').strip()
                }
        ) as resp:
            if resp.status != 200:
                return await mess.edit(
                    content=f'{tick(False)} I cannot find lyrics for the current track.', delete_after=10)

            data = (await resp.json())['response']['hits'][0]['result']
            song_url = urljoin('https://genius.com', data['path'])

        async with self.bot.session.get(song_url) as res:
            if res.status != 200:
                return await mess.edit(
                    content=f'{tick(False)} I cannot find lyrics for the current track.', delete_after=10)

            html = await res.text()

        lyrics_data = self._extract_lyrics(html)

        if lyrics_data is None:
            return await mess.edit(
                content=f'{tick(False)} I cannot find lyrics for the current track.', delete_after=10)

        mapped = list(map(lambda i: str(lyrics_data)[i: i + 4096], range(0, len(lyrics_data), 4096)))

        class TextPaginator(BasePaginator):
            async def format_page(self, entries: List, /) -> discord.Embed:
                embed = discord.Embed(title=data['full_title'],
                                      url=song_url,
                                      description=entries[0],
                                      colour=helpers.Colour.teal())
                embed.set_thumbnail(url=data['header_image_url'])
                return embed

        await mess.delete()
        await TextPaginator.start(ctx, entries=mapped, per_page=1, ephemeral=True)


async def setup(bot: RoboHashira):
    await bot.add_cog(Music(bot))
