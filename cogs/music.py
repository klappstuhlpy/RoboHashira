from __future__ import annotations
import asyncio
import logging
import random
from typing import Literal, Optional, Union, List, cast, TYPE_CHECKING
import datetime
import discord
import wavelink
from discord import app_commands

from discord.ext import commands, tasks
from discord.ext.commands._types import BotT  # noqa

from .utils.context import Context
from .utils import checks, converters, formats, errors, _commands
from .utils.render import Render
from cogs.utils.player import Player, PlayingState, PlayerPanel
from bot import RoboHashira
from cogs.utils.paginator import BasePaginator

if TYPE_CHECKING:
    from .playlist import PlaylistTools

log = logging.getLogger(__name__)


class PlayFlags(commands.FlagConverter):
    """Flags for the music commands."""
    query: str = commands.Flag(name='query', description='The query to search for.', aliases=['q'])
    source: Literal['yt', 'sp', 'sc'] = commands.Flag(
        name='source', description='The type of search you want to do. (Default: YouTube)', default='yt')
    force: Optional[bool] = commands.Flag(name='force', description='Play the track immediately from the queue.',
                                          default=False)


class Music(commands.Cog):
    """Commands for playing music in a voice channel."""

    def __init__(self, bot: RoboHashira):
        self.bot: RoboHashira = bot
        self.render: Render = Render  # type: ignore

    async def cog_check(self, ctx: Context) -> bool:
        if not ctx.guild:
            return False
        return True

    async def cog_before_invoke(self, ctx: Context[BotT]) -> None:
        playlist_tools: PlaylistTools = self.bot.get_cog('PlaylistTools')  # type: ignore
        await playlist_tools.initizalize_user(ctx.author)  # noqa

    async def cog_load(self) -> None:
        self.cleanup_players.start()

    def cog_unload(self) -> None:
        self.cleanup_players.cancel()

    @tasks.loop(hours=2)
    async def cleanup_players(self):
        await self.bot.wait_until_ready()
        node = wavelink.Pool.get_node()

        if not node:
            return

        inactive = 0

        for player in node.players.values():
            if player.playing:
                return

            if not player.playing and len(player.channel.members) == 1:
                await player.disconnect()
                inactive += 1

        log.debug(f'Cleaned up {inactive} players.')

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
            if payload.code == 1000:  # Indicates the Websocket was closed normally (no error)
                return

        player: Player | None = cast(Player, payload.player)

        if player:
            try:
                await player.view.update(PlayingState.STOPPED)
                await player.disconnect()
            except:
                pass

        args = ['%s=%r' % (k, v) for k, v in vars(payload).items()]
        log.warning(f'Wavelink Error Occured: {payload.__class__.__name__} | {', '.join(args)}')

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        logging.info(f'Wavelink Node connected: {payload.node.uri} | Resumed: {payload.resumed}')

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player: Player | None = cast(Player, payload.player)

        if not player:
            return

        if player.queue.listen_together.enabled:
            member = await self.bot.get_or_fetch_member(
                player.guild, player.queue.listen_together.user_id)
            if (activity := next((a for a in member.activities if isinstance(a, discord.Spotify)), None)) is None:
                await player.view.update(PlayingState.STOPPED)
                await player.disconnect()
                return

            try:
                track = await player.search(activity.track_url)
            except:
                await player.view.channel.send('I couldn\'t find the track you were listening to on Spotify.')
                return

            player.reset_queue()
            await player.queue.put_wait(track)
            await player.play(player.queue.get())
            return await player.send_track_add(track)

        # This is a custom shuffle to preserve
        # insert order of the tracks to the queue
        # This only plays random tracks by indexing tracks
        # with random numbers in the queue.

        # This makes it possible for the user to turn of shuffle and still have
        # the original insert order of tracks in the queue.
        if player.queue.shuffle:
            queue = player.queue.all
            # Get the next random choosen track out of all songs in the queue
            next_random_track = queue[random.randint(0, len(queue) - 1)]
            # Apply the new order to the queue
            player.queue.history.clear()
            await player.queue.history.put_wait(queue[:queue.index(next_random_track)])

            player.queue.clear()
            await player.queue.put_wait(queue[queue.index(next_random_track):])

            # Let autoplay handle the rest ...

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player: Player | None = cast(Player, payload.player)

        if not player:
            # Shouldn't happen, would likely be a connection error/downtime of the bot
            return

        if player.current.recommended:
            player.queue.history.put(player.current)

        while not player.queue.all or player.current not in player.queue.all:
            # Prevent the queue not being filled and causing
            # the Playerpanel to be throwing index errors
            await asyncio.sleep(0.1)

        await player.view.update()

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        await self.bot.wait_until_ready()

        player: Player | None = cast(Player, before.guild.voice_client)

        if not player:
            return

        listen_together = player.queue.listen_together

        if not listen_together.enabled:
            return

        if before.id == listen_together.user_id:
            before_activity = next((a for a in before.activities if isinstance(a, discord.Spotify)), None)
            after_activity = next((a for a in after.activities if isinstance(a, discord.Spotify)), None)

            if before_activity and after_activity and before_activity.title == after_activity.title:
                now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
                start = after_activity.start.replace(tzinfo=None)
                end = after_activity.end.replace(tzinfo=None)

                position = round((end - now).total_seconds()) * 1000 if now > end else round(
                    (now - start).total_seconds()) * 1000

                await player.seek(position)
                await player.view.update()
            else:
                await player.stop()
        else:
            before_activity = next((a for a in before.activities if isinstance(a, discord.Spotify)), None)

            await player.pause(True)
            await player.view.channel.send(
                '<a:loading:1072682806360166430> The Host has paused/stopped listening to Spotify.\n'
                f'*Destroying the session {formats.format_dt(datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=22), style='R')} if the host doesn\'t start listening again.*',
                delete_after=21)

            timer = 0
            new_activity = None

            while timer < 20:
                new_activity = next((a for a in after.activities if isinstance(a, discord.Spotify)), None)
                if new_activity:
                    break
                await asyncio.sleep(1)
                timer += 1

            if new_activity and new_activity.title == before_activity.title:
                await player.pause(False)
            else:
                player.reset_queue()

                try:
                    track = await player.search(new_activity.track_url)
                except:
                    await player.view.channel.send(
                        '<:redTick:1079249771975413910> I couldn\'t find the track you were listening to on Spotify.',
                        delete_after=10)
                    return

                await player.queue.put_wait(track)
                await player.send_track_add(track)
                await player.play(player.queue.get())

                position = round(
                    (datetime.datetime.now(datetime.UTC) - new_activity.start.replace(
                        tzinfo=None)).total_seconds()) * 1000
                await player.seek(position)

        if not player.connected:
            await player.view.channel.send('The host has stopped listening to Spotify.')
            await player.disconnect()

    async def join(self, ctx: discord.Interaction | Context) -> Player:
        channel = ctx.user.voice.channel if ctx.user.voice else None
        if not channel:
            if isinstance(ctx, Context):
                raise errors.BadArgument('You need to be in a voice channel or provide one to connect to.')
            else:
                raise app_commands.AppCommandError('You need to be in a voice channel or provide one to connect to.')

        player = await channel.connect(cls=Player(self.bot), self_deaf=True)

        if isinstance(channel, discord.StageChannel):
            if not channel.instance:
                await channel.create_instance(topic=f'Music by {ctx.guild.me.display_name}')
            await ctx.guild.me.edit(suppress=False)

        player.view = await PlayerPanel.start(player)
        await player.view.fetch_player_channel(ctx.channel)
        return player

    @_commands.command(
        description='Adds a track/playlist to the queue and play the next available track.',
        guild_only=True
    )
    @app_commands.describe(query='The track/playlist to add to the queue.',
                           source='The type of search to perform.',
                           force='Force the track to be added to the queue.')
    @app_commands.choices(
        source=[
            app_commands.Choice(name='YouTube (Default)', value='yt'),
            app_commands.Choice(name='Spotify', value='sp'),
            app_commands.Choice(name='SoundCloud', value='sc')])
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
        # TODO: Remove YouTube for Source when Bot gets verified
        sources = {
            'yt': wavelink.TrackSource.YouTubeMusic,
            'sp': 'spsearch',
            'sc': wavelink.TrackSource.SoundCloud
        }
        flags.source = sources.get(flags.source, wavelink.TrackSource.YouTubeMusic)

        player: Player = cast(Player, ctx.voice_client)

        if not player:
            player = await self.join(ctx)

        player.autoplay = wavelink.AutoPlayMode.enabled

        if not flags.query:
            await ctx.stick(False, 'Please provide a search query.', ephemeral=True,
                            delete_after=10)
            return

        query = flags.query.strip('<>')
        result = await player.search(query, flags.source, ctx)

        if result is None:
            await ctx.stick(False, 'Sorry! No results found matching your query.',
                            ephemeral=True, delete_after=10)
            return

        if await player.check_blacklist(result):
            await ctx.send(
                '<:redTick:1079249771975413910> Blacklisted Track detected. Please try another one.',
                ephemeral=True, delete_after=10)
            return

        if isinstance(result, wavelink.Playlist):
            before_count = len(player.queue.all)

            result.track_extras(requester=ctx.author)
            added: int = await player.queue.put_wait(result)

            embed = discord.Embed(title='Playlist Enqueued',
                                  description=f'`🎶` Enqueued successfully **{added}** tracks from [{result.name}]({result.url}).\n'
                                              f'`🎵` *Next Track at Position **#{before_count + 1}/{len(player.queue.all)}***',
                                  color=formats.Colour.teal())
            if result.artwork:
                embed.set_thumbnail(url=result.artwork)
            embed.set_footer(text=f'Requested by: {ctx.author}', icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed, delete_after=15)
        else:
            setattr(result, 'requester', ctx.author)
            if flags.force:
                player.queue.put_at_front(result)
            else:
                await player.queue.put_wait(result)

            await player.send_track_add(result, ctx)

        if player.playing and flags.force:
            await player.skip()
        elif not player.playing:
            await player.play(player.queue.get(), volume=70)
        else:
            await player.view.update()

    listen_together = app_commands.Group(name='listen-together',
                                         description='Listen-together related commands.')

    @_commands.command(
        listen_together.command,
        name='start',
        description='Start a listen-together activity with a user.',
        guild_only=True
    )
    @app_commands.describe(member='The user you want to start a listen-together activity with.')
    @checks.is_author_connected()
    async def listen_together_start(self, interaction: discord.Interaction, member: discord.Member):
        """Start a listen-together activity with a user.
        `!:` Only Supported for Spotify Music."""
        if not interaction.guild.voice_client:
            await self.join(interaction)

        player: Player = cast(Player, interaction.guild.voice_client)
        if not player:
            return

        member = await self.bot.get_or_fetch_member(interaction.guild, member.id)

        if not (activity := next((a for a in member.activities if isinstance(a, discord.Spotify)), None)):
            await interaction.response.send_message(
                '<:redTick:1079249771975413910> The User isn\'t playing anything right now.', ephemeral=True,
                delete_after=10)
            return

        if player.playing or player.queue.listen_together.enabled:  # noqa
            player.reset_queue()
            await player.stop()

        player.autoplay = wavelink.AutoPlayMode.disabled

        try:
            track = await player.search(activity.track_url)
        except:
            await interaction.response.send_message(
                '<:redTick:1079249771975413910> The User isn\'t playing anything right now.', ephemeral=True,
                delete_after=10)
            return

        setattr(track, 'requester', interaction.user)
        await player.queue.put_wait(track)
        player.queue.listen_together = {'state': True, 'user_id': member.id}
        await player.play(player.queue.get())

        poss = round(
            (datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - activity.start.replace(tzinfo=None)
             ).total_seconds()) * 1000
        await player.seek(poss)

        await player.send_track_add(track, interaction)

    @_commands.command(
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

        if player.queue.listen_together.enabled:
            await player.view.update(PlayingState.STOPPED)
            await player.disconnect()

            await interaction.response.send_message(
                f'<:greenTick:1079249732364406854> Stopped the current listen-together activity.', delete_after=10)
        else:
            return await interaction.response.send_message(
                '<:redTick:1079249771975413910> There is currently no listen-together activity started.',
                ephemeral=True, delete_after=10)

    @_commands.command(name='connect', description='Connect me to a voice-channel.', guild_only=True)
    @app_commands.describe(channel='The Voice/Stage-Channel you want to connect to.')
    async def connect(self, ctx: Context, channel: Union[discord.VoiceChannel, discord.StageChannel] = None):
        """Connect me to a voice-channel."""
        if ctx.voice_client:
            await ctx.stick(False, 'I am already connected to a voice channel. Please disconnect me first.')
            return

        try:
            channel = channel or ctx.author.voice.channel
        except AttributeError:
            await ctx.stick(False, 'No voice channel to connect to. Please either provide one or join one.')
            return

        await self.join(ctx)
        await ctx.stick(True, f'Connected and bound to {channel.mention}', delete_after=10)

    @_commands.command(description='Disconnect me from a voice-channel.', guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_connected()
    async def leave(self, ctx: Context):
        """Disconnect me from a voice-channel."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await player.view.update(PlayingState.STOPPED)
        await player.disconnect()
        await ctx.stick(True, 'Disconnected Channel and cleaned up the queue.', delete_after=10)

    @_commands.command(name='stop', description='Clears the queue and stop the current plugins.', guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def stop(self, ctx: Context):
        """Clears the queue and stop the current plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await player.view.stop()
        await player.disconnect()
        await ctx.stick(True, 'Stopped Track and cleaned up queue.', delete_after=10)

    @_commands.command(
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
        await ctx.stick(True, f'{'Paused' if player.paused else 'Resumed'} Track '
                              f'[{player.current.title}]({player.current.uri})',
                        delete_after=10, suppress_embeds=True)
        await player.view.update()

    @_commands.command(description='Sets a loop mode for the plugins.', guild_only=True)
    @app_commands.describe(mode='Select a loop mode.')
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def loop(self, ctx: Context, mode: Literal['Normal', 'Track', 'Queue']):
        """Sets a loop mode for the plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if mode == 'Normal':
            player.queue.mode = wavelink.QueueMode.normal
        elif mode == 'Track':
            player.queue.mode = wavelink.QueueMode.loop
        elif mode == 'Queue':
            player.queue.mode = wavelink.QueueMode.loop_all

        await player.view.update()
        await ctx.stick(True, f'Loop Mode changed to `{mode}`', delete_after=10)

    @_commands.command(description='Sets the shuffle mode for the plugins.', guild_only=True)
    @app_commands.describe(mode='Select a shuffle mode.')
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def shuffle(self, ctx: Context, mode: Literal['True', 'False']):
        """Sets the shuffle mode for the plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        player.queue.shuffle = True if mode == 'True' else False
        await player.view.update()
        await ctx.stick(True, f'Shuffle Mode changed to `{mode}`', delete_after=10)

    @_commands.command(description='Seek to a specific position in the tack.', guild_only=True)
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
            await ctx.stick(False, 'Cannot seek if track is a stream.', ephemeral=True, delete_after=10)
            return

        if position is None:
            await player.seek(0)
        else:
            try:
                seconds = sum(int(x) * 60 ** i for i, x in enumerate(reversed(position.split(':'))))
            except ValueError:
                await ctx.stick(False, 'Please provide a valid TimeStamp format.', ephemeral=True)
                return

            seconds = int(seconds) * 1000
            if seconds in range(int(player.current.length)):
                await player.seek(seconds)
            else:
                await ctx.stick(False, 'Please provide a seek time within the range of the track.',
                                ephemeral=True, delete_after=10)
                return

        await ctx.stick(True, f'Seeked to position ``{converters.convert_duration(seconds)}``',  # noqa
                        delete_after=10)

        await player.view.update()

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

        to_return = [app_commands.Choice(
            name=datetime.datetime.fromtimestamp(int(seconds), datetime.UTC).strftime('%H:%M:%S'),
            value=datetime.datetime.fromtimestamp(int(seconds), datetime.UTC).strftime('%H:%M:%S'))]
        return to_return

    @_commands.command(description='Set the volume for the plugins.', guild_only=True)
    @app_commands.describe(amount='The volume to set the plugins to. (0-100)')
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def volume(self, ctx: Context, amount: Optional[str] = None):
        """Set the volume for the plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if amount is None:
            embed = discord.Embed(title=f'Current Volume', color=formats.Colour.teal())
            embed.add_field(name=f'Volume:',
                            value=f'```swift\n{converters.VisualStamp(0, 100, player.volume)} [ {player.volume}% ]```',
                            inline=False)
            await ctx.send(embed=embed, delete_after=10)
            return

        def valid_volume(vol: str):
            if vol.startswith('+') or vol.startswith('-'):
                if vol[1:].isdigit():
                    return True
            else:
                if vol.isdigit():
                    return True
            return False

        if not valid_volume(amount):
            embed = discord.Embed(
                title='Invalid Input',
                description=f'<:redTick:1079249771975413910> The Input `{amount}` is not a valid Volume Input.\n'
                            'Please use one of the following',
                color=discord.Color.red())
            embed.add_field(name='Increase the Volume:', value='```swift\ne.g. +54```')
            embed.add_field(name='Decrease the Volume:', value='```swift\ne.g. -24```')
            embed.add_field(name='Set the Volume:', value='```swift\ne.g. 23```')
            await ctx.send(embed=embed, ephemeral=True, delete_after=10)
            return

        def format_vol(vol: str):
            if vol.startswith('+'):
                return player.volume + int(vol[1:])
            elif vol.startswith('-'):
                return player.volume - int(vol[1:])
            else:
                return int(vol)

        await player.set_volume(format_vol(amount))
        await player.view.update()

        embed = discord.Embed(title=f'Changed Volume', color=formats.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        embed.add_field(name=f'Volume:',
                        value=f'```swift\n{converters.VisualStamp(0, 100, player.volume)} [ {player.volume}% ]```',
                        inline=False)
        await ctx.send(embed=embed, delete_after=10)

    @_commands.command(description='Removes all songs from users that are not in the voice channel.', guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def leftcleanup(self, ctx: Context):
        """Removes all songs from users that are not in the voice channel."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await player.delete_from_left()
        await player.view.update()
        await ctx.stick(True, 'Cleaned up the queue.', delete_after=10)

    @_commands.command(
        commands.hybrid_group,
        description='Manage Advanced Filters to specify you listening experience.',
        guild_only=True
    )
    @commands.guild_only()
    async def filter(self, ctx: Context):
        """Find useful information about the filter command group."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_commands.command(filter.command, name='equalizer', description='Set the equalizer for the current Track.')
    @app_commands.describe(band='The Band you want to change. (1-15)',
                           gain='The Gain you want to set. (-0.25-+1.0')
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
            await ctx.stick(False, 'Please provide a valid Band and Gain or a Preset.')
            return

        band -= 1

        eq = filters.equalizer.payload
        eq[band]['gain'] = gain
        filters.equalizer.set(bands=[dicT for dicT in eq.values()])
        await player.set_filters(filters)

        embed = discord.Embed(title=f'Changed Filter', color=formats.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        file = discord.File(
            fp=self.render.generate_eq_image([entry['gain'] for entry in filters.equalizer.payload.values()]),
            filename='image.png')
        embed.set_image(url='attachment://image.png')
        embed.set_footer(text=f'Requested by: {ctx.author}')
        await ctx.send(embed=embed, file=file, delete_after=20)

    @_commands.command(filter.command, name='bassboost', description='Enable/Disable the bassboost filter.')
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

        embed = discord.Embed(title=f'Changed Filter', color=formats.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        file = discord.File(
            fp=self.render.generate_eq_image([entry['gain'] for entry in filters.equalizer.payload.values()]),
            filename='image.png')
        embed.set_image(url='attachment://image.png')
        embed.set_footer(text=f'Requested by: {ctx.author}')
        await ctx.send(embed=embed, file=file, delete_after=20)

    @_commands.command(filter.command, name='nightcore', description='Enables/Disables the nightcore filter.')
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

        embed = discord.Embed(title=f'Changed Filter', color=formats.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        await ctx.send(embed=embed, delete_after=10)

    @_commands.command(filter.command, name='8d', description='Enable/Disable the 8d filter.')
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

        embed = discord.Embed(title=f'Changed Filter', color=formats.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        await ctx.send(embed=embed, delete_after=10)

    @_commands.command(
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

        embed = discord.Embed(title=f'Changed Filter', color=formats.Colour.teal(),
                              description='*It may takes a while for the changes to apply.*')
        embed.add_field(name=f'Applied LowPass Filter:',
                        value=f'Set Smoothing to ``{smoothing}``.',
                        inline=False)
        await ctx.send(embed=embed, delete_after=10)

    @_commands.command(filter.command, name='reset', description='Reset all active filters.')
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

    @_commands.command(description='Skip the playing song to the next.', guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def forceskip(self, ctx: Context):
        """Skip the playing song."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if not player.queue.future_is_empty:
            await ctx.stick(True, 'An admin or DJ has to the next track.', delete_after=10)
            await player.skip()
        else:
            await ctx.stick(False, 'The queue is empty.', ephemeral=True, delete_after=10)

    @_commands.command(name='jump-to', description='Jump to a track in the Queue.', guild_only=True)
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

        if not player.queue.all_is_empty:
            if position < 0:
                await ctx.stick(False, 'The index must be greater than or 0.',
                                ephemeral=True, delete_after=10)
                return

            if (position - 1) > len(player.queue.all):
                await ctx.stick(False, 'There are not that many tracks in the queue.',
                                ephemeral=True, delete_after=10)
                return

            player.queue.put_at_front(player.queue.all[position - 1])
            player.queue.all.pop(position - 1)

            await player.stop()

            if position != 1:
                await ctx.stick(True, f'Playing the **{position}** track in queue.', delete_after=10)
            else:
                await ctx.stick(True, 'Playing the next track in queue.', delete_after=10)
        else:
            return await ctx.stick(False, 'The queue is empty.', ephemeral=True, delete_after=10)

    @_commands.command(description='Plays the previous Track.', guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def back(self, ctx: Context):
        """Plays the previous Track."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if not player.queue.history_is_empty:
            await ctx.stick(True, 'An admin or DJ has skipped to the previous song.', delete_after=10)

            index = player.queue.all.index(player.current) - 1
            track_to_revert = player.queue.all[index]

            player.queue.put_at_front(track_to_revert)
            player.queue.put_at_index(index + 1, player.current)
            await player.queue.history.delete(index)
            await player.queue.history.delete(index)

            await player.stop()
        else:
            await ctx.stick(False, 'No items currently in the history of the queue.\n'
                                   'Or you are on the first position.', ephemeral=True)
            return

    @_commands.command(description='Display the active queue.', guild_only=True)
    async def queue(self, ctx: Context):
        """Display the active queue."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if player.queue.all_is_empty:
            await ctx.stick(False, 'No items currently in the queue.', ephemeral=True)
            return

        await ctx.defer()

        class QueuePaginator(BasePaginator):

            @staticmethod
            def fmt(track: wavelink.Playable, index: int) -> str:
                return (
                    f'`[ {index}. ]` [{track.title}]({track.uri}) by **{track.author or 'Unknown'}** '
                    f'[`{converters.convert_duration(track.length)}`]'
                )

            async def format_page(self, entries: List, /) -> discord.Embed:
                embed = discord.Embed(color=formats.Colour.teal())
                embed.set_author(name=f'{ctx.guild.name}\'s Current Queue', icon_url=ctx.guild.icon.url)

                embed.add_field(
                    name='╔ Now Playing:',
                    value=f'[{player.current.title}]({player.current.uri}) '
                          f'by **{player.current.author or 'Unknown'}** '
                          f'[`{converters.convert_duration(player.current.length)}`]')

                embed.add_field(
                    name='╠ Up Next:',
                    value='\n'.join(
                        self.fmt(track, i) for i, track in enumerate(entries, 1)
                    ) or '*It seems like there are currently not upcomming tracks.*\n'
                         'Add one with </play:1079059790380142762>.',
                    inline=False)

                embed.add_field(name='╚ Settings:', value=f'DJ: {player.dj.mention}', inline=False)
                embed.set_footer(text=f'Total: {len(player.queue.all)} • History: {len(player.queue.history) - 1}')
                return embed

        await QueuePaginator.start(ctx, entries=player.queue, per_page=30)

    @_commands.command(description='Search for some lyrics.')
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

        mess = await ctx.send(content=f'\🔎 *Searching lyrics for lyrics...*')

        headers = {'X-RapidAPI-Key': self.bot.config.rapidapi.api_key,
                   'X-RapidAPI-Host': self.bot.config.rapidapi.api_host}

        async with self.bot.session.get(
                f'https://geniuslyrics-api.p.rapidapi.com/search_songs',
                headers=headers, params={'song': song or player.current.title}) as resp:
            if resp.status != 200:
                await mess.delete()
                await ctx.stick(False, 'I cannot find lyrics for the current track.',
                                ephemeral=True, delete_after=10)
                return
            song: dict = (await resp.json())['hits'][0]

            async with self.bot.session.get(
                    f'https://geniuslyrics-api.p.rapidapi.com/get_lyrics',
                    headers=headers, params={'song_id': song['songID']}) as resp:
                data = await resp.json()

            # TODO: Lyrics Endpoint is currently under maintenance

            if data.get('error'):
                await ctx.stick(False, 'I cannot find lyrics for the current track.',
                                ephemeral=True, delete_after=10)
                return

                # mapped = list(map(lambda i: str(data['lyrics'])[i: i + 4096], range(0, len(str(data['lyrics'])), 4096)))
            mapped = []
            await mess.delete()

            class TextPaginator(BasePaginator):

                async def format_page(self, entries: List, /) -> discord.Embed:
                    embed = discord.Embed(title=song['songTitle'],
                                          description=entries[0],
                                          colour=formats.Colour.teal())
                    embed.set_thumbnail(url=song['songImageURL'])
                    return embed

            await TextPaginator.start(ctx, entries=mapped, per_page=1, ephemeral=True)


async def setup(bot: RoboHashira):
    await bot.add_cog(Music(bot))
