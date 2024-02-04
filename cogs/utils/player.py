from __future__ import annotations

import enum
import datetime
from typing import TYPE_CHECKING, Any, Optional, List, Generic, Type, TypeVar

import discord
import wavelink
import yarl
from discord.ext import commands
from discord.utils import MISSING
from wavelink import QueueMode

from cogs.utils import formats, helpers
from cogs.utils.formats import truncate
from cogs.config import GuildConfig
from cogs.utils import converters
from cogs.utils.context import Context, tick
from cogs.utils.queue import Queue, ShuffleMode
from launcher import get_logger

if TYPE_CHECKING:
    from bot import RoboHashira

log = get_logger(__name__)
T = TypeVar('T')


def is_dj(member: discord.Member) -> bool:
    """Checks if the Member has the DJ Role."""
    role = discord.utils.get(member.guild.roles, name='DJ')
    return role in member.roles


def to_emoji(index: int) -> str:
    return f'{index + 1}️⃣'


EMOJI_KEYS = {
    'shuffle': {
        ShuffleMode.on: discord.PartialEmoji(name='shuffle', id=1068273347919630417),
        ShuffleMode.off: discord.PartialEmoji(name='shuffleNone', id=1068273345507905607)
    },
    'pause_play': {
        True: '⏸️',
        False: '▶️'
    },
    'loop': {
        QueueMode.loop: discord.PartialEmoji(name='repeatTrack', id=1066048250529972355),
        QueueMode.loop_all: discord.PartialEmoji(name='repeatAll', id=1066048247585575002),
        QueueMode.normal: discord.PartialEmoji(name='repeatNone', id=1066048246235013231)
    },
    'like': {
        True: discord.PartialEmoji(name='liked', id=1183539703333535764),
        False: discord.PartialEmoji(name='un_liked', id=1183539705023836250)
    }
}


def source_emoji(source: str) -> discord.PartialEmoji:
    return {
        'youtube': discord.PartialEmoji(name='YouTube', id=1066146818884382770),
        'spotify': discord.PartialEmoji(name='spotify', id=1066177938858455090),
        'soundcloud': discord.PartialEmoji(name='soundcloud', id=1066184529452204093)
    }.get(source, discord.PartialEmoji(name='offline', id=1085666365689573438))


class PlayerState(enum.Enum):
    PLAYING = 1
    PAUSED = 2
    STOPPED = 3


class Player(wavelink.Player):
    """Custom mdded-wavelink Player class."""

    def __init__(self, bot: RoboHashira):
        super().__init__()
        self.bot: RoboHashira = bot

        self.panel: PlayerPanel = MISSING
        self.queue: Queue = Queue()

        self.dj: discord.Member | discord.abc.User = self.bot.user

    @property
    def connected(self) -> bool:
        """Returns True if the player is connected to a voice channel."""
        return self.channel is not None

    @classmethod
    async def search(
            cls,
            query: str,
            *,
            source: wavelink.TrackSource | str = wavelink.TrackSource.YouTubeMusic,
            ctx: Optional[discord.Interaction, Context] = None,
            return_first: bool = False
    ) -> wavelink.Playable | wavelink.Playlist | None:
        """Searches for a keyword/url on YouTube, Spotify, or SoundCloud.

        Parameters
        ----------
        query : str
            The keyword or URL to search for.
        source : wavelink.TrackSource | str
            The source to search from.
        ctx : Optional[discord.Interaction, Context]
            The context of the command.
        return_first : bool
            Whether to return the first result if it's a list.

        Returns
        -------
        wavelink.Playable | wavelink.Playlist | None
            The result of the search.
        """
        check = yarl.URL(query)
        is_url = bool(check and check.host and check.scheme)

        try:
            if not is_url:
                results = await wavelink.Playable.search(query, source=source)
                if return_first and isinstance(results, list):
                    results = results[0]
                else:
                    results = await TrackDisambiguatorView.start(
                        ctx, tracks=results.tracks if isinstance(results, wavelink.Playlist) else results
                    ) if ctx else results
            else:
                results = await wavelink.Playable.search(query)
        except Exception as exc:
            log.error(f'Error while searching for {query!r}', exc_info=exc)
            return None

        if not results:
            return None

        if isinstance(results, list) and is_url:
            results = results[0]

        return results

    async def check_blacklist(self, result: wavelink.Playable | wavelink.Playlist) -> bool:
        """Returns True if the track is on the blacklist."""
        BLACKLISTED = {
            record['url'] for record in await self.bot.pool.fetch('SELECT url FROM track_blacklist')
        }
        if isinstance(result, wavelink.Playlist):
            return any(track.uri in BLACKLISTED for track in result.tracks)
        else:
            return result.uri in BLACKLISTED

    async def disconnect(self, **kwargs) -> None:
        """Disconnects the player from the voice channel."""
        if self.playing:
            await self.stop()

        if self.panel is not MISSING and self.panel.state != PlayerState.STOPPED:
            await self.panel.stop()

        await super().disconnect(**kwargs)

    async def cleanupleft(self):
        """Removes all tracks from the queue that are not in the voice channel."""
        listeners = self.channel.members

        for i, track in enumerate(self.queue.history):
            if track.requester not in listeners:  # noqa
                self.queue.history.delete(i)

        for i, track in enumerate(self.queue):
            if track.requester not in listeners:  # noqa
                self.queue.delete(i)
                if self.current == track:
                    await self.stop()

    def jump_to(self, index: int) -> bool:
        """Jumps to a specific track in the queue.

        Parameters
        ----------
        index : int
            The index to jump to.

        Returns
        -------
        bool
            Whether the jump was successful.
        """
        if index < 0 or index >= len(self.queue.all):
            return False

        self.queue.put_at(0, self.queue.all[index])
        self.queue.delete(index + 1)
        return True

    async def send_track_add(
            self, track: wavelink.Playable, obj: Optional[Context | discord.Interaction] = None
    ) -> discord.Message | None | Any:
        embed = discord.Embed(
            title='Track Enqueued',
            description=f'`🎶` Added [{track.title}]({track.uri}) to the queue.\n'
                        f'`🎵` Track at Position **#{self.queue.all.index(track) + 1}/{len(self.queue.all)}**',
            color=helpers.Colour.teal()
        )

        if track.artwork:
            embed.set_thumbnail(url=track.artwork)

        if not obj:
            return await self.panel.channel.send(embed=embed)

        embed.set_footer(text=f'Requested by {obj.user}', icon_url=obj.user.display_avatar.url)

        if isinstance(obj, Context):
            return await obj.send(embed=embed, delete_after=15)
        else:
            if obj and obj.response.is_done():
                return await obj.followup.send(embed=embed, delete_after=15)
            else:
                return await obj.response.send_message(embed=embed, delete_after=15)


class PlayerPanel(discord.ui.View, Generic[T]):
    """
    The Main Class for a Player Panel.

    Attributes
    ----------
    player: :class:`Player`
        The player that this panel is for.
    bot: :class:`RoboHashira`
        The bot instance.
    """

    def __init__(self, *, player: Player, state: PlayerState) -> None:
        super().__init__(timeout=None)
        self.bot: RoboHashira = player.bot

        self.player: Player = player
        self.state: PlayerState = state

        self.msg: discord.Message = MISSING
        self.channel: discord.TextChannel = MISSING

        self.cooldown: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(2, 5, lambda ctx: ctx.user)

        self.update_buttons()

    @property
    def build_message(self) -> dict[str, Any]:
        if self.state == PlayerState.PLAYING:
            track = self.player.current

            embed = discord.Embed(
                title='Music Player Panel',
                description='This is the Bot\'s control panel where you can easily perform actions '
                            'of the bot without using a command.',
                timestamp=discord.utils.utcnow(),
                color=self.bot.colour.teal()
            )

            artist = f'[{track.author}]({track.artist.url})' if track.artist.url else track.author

            embed.add_field(
                name='╔ Now Playing:',
                value=f'╠ **Track:** [{track.title}]({track.uri})\n'
                      f'╠ **Artist:** {artist}\n'
                      f'╠ **Bound to:** {self.player.channel.mention}\n'
                      f'╠ **Position in Queue:** {self.player.queue.all.index(self.player.current) + 1}/{len(self.player.queue.all)}',
                inline=False
            )

            if track.album and track.album.name:
                embed.add_field(
                    name='╠ Album:',
                    value=f'[{track.album.name}]({track.album.url})' if track.album.url else track.album.name,
                    inline=False
                )

            if track.playlist:
                embed.add_field(
                    name='╠ Playlist:',
                    value=f'[{track.playlist.name}]({track.playlist.url})' if track.playlist.url else track.playlist.name,
                    inline=False
                )

            if self.player.queue.listen_together is not MISSING:
                user = self.player.guild.get_member(self.player.queue.listen_together)
                embed.add_field(name='╠ Listening-together with:', value=f'{user.mention}\'s Spotify', inline=False)

            embed.add_field(
                name='╠ Status:',
                value=f'```swift\n{formats.player_stamp(track.length, self.player.position)}```'
                if not track.is_stream else '```swift\n[ 🔴 LIVE STREAM ]```',
                inline=False)

            loop_mode = self.player.queue.mode.name.replace('_', ' ').upper()
            embed.add_field(name='╠ Loop Mode:', value=f'`{loop_mode}`')
            embed.add_field(
                name='═ Shuffle Mode:',
                value={
                    ShuffleMode.off: '<:off1:1077001786184974356> **``Off``**',
                    ShuffleMode.on: '<:on1:1077001788051423293> **``On``**'
                }.get(self.player.queue.shuffle))
            embed.add_field(
                name=f'╠ Volume:',
                value=f'```swift\n{converters.VisualStamp(0, 100, self.player.volume)} [ {self.player.volume}% ]```',
                inline=False)

            if track.recommended:
                embed.add_field(name='╠ Recommended via:',
                                value=f'{source_emoji(track.source)} **`{track.source.title()}`**',
                                inline=False)

            if upcomming := self.player.queue.loaded:
                eta = discord.utils.utcnow() + datetime.timedelta(
                    milliseconds=(self.player.current.length - self.player.position))
                embed.add_field(name='╠ Next Track:',
                                value=f'[{upcomming.title}]({upcomming.uri}) {discord.utils.format_dt(eta, 'R')}')

            if artwork := self.player.current.artwork:
                embed.set_thumbnail(url=artwork)

            # Add '╚' to the last field's name
            field = embed.fields[-1]
            embed.set_field_at(index=len(embed.fields) - 1, name='╚ ' + field.name[1:],
                               value=field.value, inline=field.inline)

            embed.set_footer(text=f'{'Auto-Playing' if self.player.autoplay != 2 else 'Manual-Playing'} • last updated')
        else:
            embed = discord.Embed(
                title='Music Player Panel',
                description='The control panel was closed, the queue is currently empty and I got nothing to do.\n'
                            'You can start a new player session by invoking the </play:1079059790380142762> command.\n\n'
                            '*Once you play a new track, this message is going to be the new player panel if it\'s not deleted, otherwise I\'m going to create a new panel.*',
                timestamp=discord.utils.utcnow(),
                color=self.bot.colour.teal()
            )
            embed.set_footer(text='last updated')
            embed.set_thumbnail(url=self.player.guild.icon.url if not None else None)

        return {'embed': embed, 'view': self}

    def disabled_state(self, check: bool = None) -> bool:
        return check or bool(self.state == PlayerState.STOPPED) or self.player.queue.all_is_empty

    def update_buttons(self):
        button_updates = [
            (self.on_shuffle, self.disabled_state(), EMOJI_KEYS['shuffle'][self.player.queue.shuffle]),
            (self.on_back, self.disabled_state(self.player.queue.history_is_empty), None),
            (self.on_pause_play, self.disabled_state(), EMOJI_KEYS['pause_play'][
                True if not self.player.paused and self.player.playing else False]),
            (self.on_forward, self.disabled_state(self.player.queue.is_empty), None),
            (self.on_loop, self.disabled_state(), EMOJI_KEYS['loop'][self.player.queue.mode]),
            (self.on_stop, self.disabled_state(), None),
            (self.on_volume, self.disabled_state(), None),
            (self.on_like, self.disabled_state(), None)
        ]

        for button, disabled, emoji in button_updates:
            button.disabled = disabled
            if hasattr(button, 'emoji') and emoji is not None:
                button.emoji = emoji

    async def interaction_check(self, interaction: discord.Interaction):
        assert isinstance(interaction.user, discord.Member)

        author_vc = interaction.user.voice and interaction.user.voice.channel
        bot_vc = interaction.guild.me.voice and interaction.guild.me.voice.channel

        if retry_after := self.cooldown.update_rate_limit(interaction):
            await interaction.response.send_message(
                f'{tick(False)} You are being rate limited. Try again in **{retry_after:.2f}** seconds.',
                ephemeral=True
            )
            return False

        if is_dj(interaction.user) and bot_vc and (not author_vc):
            return True

        if author_vc and bot_vc and (author_vc == bot_vc) and (
                interaction.user.voice.deaf or interaction.user.voice.self_deaf):
            await interaction.response.send_message(
                f'{tick(False)} You are deafened, please undeafen yourself to use this menu.',
                ephemeral=True
            )
            return False
        elif (not author_vc and bot_vc) or (author_vc and bot_vc and author_vc != bot_vc):
            await interaction.response.send_message(
                f'{tick(False)} You must be in {bot_vc.mention} to use this menu.',
                ephemeral=True
            )
            return False
        elif not author_vc:
            await interaction.response.send_message(
                f'{tick(False)} You must be in a voice channel to use this menu.',
                ephemeral=True
            )
            return False

        return True

    async def fetch_player_channel(self, channel: Optional[discord.TextChannel] = None) -> Optional[discord.TextChannel]:
        """Gets the channel where the player is currently playing."""
        config: GuildConfig = await self.bot.cfg.get_config(self.player.guild.id)

        self.channel = self.player.guild.get_channel(config.music_channel) if config.music_channel else channel
        return self.channel

    async def get_player_message(self) -> Optional[discord.Message]:
        """Gets the message of the current plugin's control panel."""
        config: GuildConfig = await self.bot.cfg.get_config(self.player.guild.id)

        if self.channel is MISSING:
            await self.fetch_player_channel()

        if self.msg is MISSING and config.music_message_id:
            try:
                self.msg = await self.channel.fetch_message(config.music_message_id)
            except discord.NotFound:
                return

        return self.msg

    async def update(self, state: PlayerState = PlayerState.PLAYING) -> discord.Message | None | Any:
        self.state = state
        self.update_buttons()

        # Only awaits if `get_player_message` has side effects or the result is needed
        await self.get_player_message()

        if self.msg is not MISSING:
            await self.msg.edit(**self.build_message)
        else:
            self.msg = await self.channel.send(**self.build_message)

        return self.msg

    async def stop(self) -> None:
        self.player.queue.reset()
        await self.update(PlayerState.STOPPED)

        super().stop()

    @discord.ui.button(
        style=discord.ButtonStyle.grey,
        emoji=EMOJI_KEYS['shuffle'][ShuffleMode.off],
        disabled=True
    )
    async def on_shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        TOGGLE = {
            ShuffleMode.off: ShuffleMode.on,
            ShuffleMode.on: ShuffleMode.off
        }
        self.player.queue.shuffle = TOGGLE.get(self.player.queue.shuffle)

        self.update_buttons()
        await interaction.response.edit_message(**self.build_message)

    @discord.ui.button(
        style=discord.ButtonStyle.blurple,
        emoji='⏮️',
        disabled=True
    )
    async def on_back(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        index = self.player.queue.all.index(self.player.current) - 1
        track_to_revert = self.player.queue.all[index]

        self.player.queue.put_at(0, track_to_revert)
        self.player.queue.put_at(index + 1, self.player.current)

        for _ in range(2):
            self.player.queue.history.delete(index)

        self.update_buttons()
        await interaction.response.edit_message(**self.build_message)
        await self.player.stop()

    @discord.ui.button(
        style=discord.ButtonStyle.blurple,
        emoji=EMOJI_KEYS['pause_play'][False],
        disabled=True
    )
    async def on_pause_play(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        await self.player.pause(not self.player.paused)
        self.update_buttons()
        await interaction.response.edit_message(**self.build_message)

    @discord.ui.button(
        style=discord.ButtonStyle.blurple,
        emoji='⏭',
        disabled=True
    )
    async def on_forward(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        await interaction.response.edit_message(**self.build_message)
        await self.player.skip()

    @discord.ui.button(
        style=discord.ButtonStyle.grey,
        emoji=EMOJI_KEYS['loop'][QueueMode.normal],
        disabled=True
    )
    async def on_loop(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        TRANSITIONS = {
            QueueMode.normal: QueueMode.loop,
            QueueMode.loop: QueueMode.loop_all,
            QueueMode.loop_all: QueueMode.normal,
        }
        self.player.queue.mode = TRANSITIONS.get(self.player.queue.mode)

        self.update_buttons()
        await interaction.response.edit_message(**self.build_message)

    @discord.ui.button(
        style=discord.ButtonStyle.red,
        emoji='⏹️',
        label='Stop',
        disabled=True
    )
    async def on_stop(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        await self.stop()
        await self.player.disconnect()
        await interaction.response.send_message(
            f'{tick(True)} Stopped Track and cleaned up queue.',
            delete_after=10)

    @discord.ui.button(
        style=discord.ButtonStyle.grey,
        emoji='🔊',
        label='Adjust Volume',
        disabled=True
    )
    async def on_volume(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        await interaction.response.send_modal(AdjustVolumeModal(self))

    @discord.ui.button(
        style=discord.ButtonStyle.green,
        emoji=EMOJI_KEYS['like'][False],
        disabled=True
    )
    async def on_like(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        from cogs.playlist import PlaylistTools
        playlist_tools: PlaylistTools = self.bot.get_cog('PlaylistTools')  # noqa
        if not playlist_tools:
            return await interaction.response.send_message('This feature is currently disabled.', ephemeral=True)

        liked_songs = await playlist_tools.get_liked_songs(interaction.user.id)

        if not liked_songs:
            await playlist_tools.initizalize_user(interaction.user)

        if self.player.current.uri not in liked_songs:
            await liked_songs.add_track(self.player.current)
            await interaction.response.send_message(
                f'{tick(True)} Added `{self.player.current.title}` to your liked songs.',
                ephemeral=True)
        else:
            await liked_songs.remove_track(discord.utils.get(liked_songs.tracks, url=self.player.current.uri))
            await interaction.response.send_message(
                f'{tick(True)} Removed `{self.player.current.title}` from your liked songs.',
                ephemeral=True)

        playlist_tools.get_playlists.invalidate(playlist_tools, interaction.user.id)

    @classmethod
    async def start(
            cls: Type[PlayerPanel],
            player: Player,
            *,
            channel: discord.TextChannel,
            state: PlayerState = PlayerState.STOPPED,
    ) -> PlayerPanel[T]:
        """|coro|

        Used to start the paginator.

        Parameters
        ----------
        player: :class:`Player`
            The player to use for the panel.
        channel: :class:`discord.TextChannel`
            The channel to send the panel to. Only used if no configuration for the guild is found.
        state: :class:`PlayerState`
            The state of the player.

        Returns
        -------
        :class:`BaseButtonPaginator`[T]
            The paginator that was started.
        """
        self = cls(player=player, state=state)

        await self.fetch_player_channel(channel)

        self.msg = await self.update(state=state)
        return self


class AdjustVolumeModal(discord.ui.Modal, title='Volume Adjuster'):
    """Modal that prompts users for the volume to change to."""
    number = discord.ui.TextInput(
        label='Volume - %', style=discord.TextStyle.short, placeholder='Enter a Number between 1 and 100',
        min_length=1, max_length=3)

    def __init__(self, _view: PlayerPanel, /):
        super().__init__(timeout=30)
        self._view: PlayerPanel = _view

    async def on_submit(self, interaction: discord.Interaction, /):
        if not self.number.value.isdigit():
            return await interaction.response.send_message('Please enter a valid number.', ephemeral=True)

        value = int(self.number.value)
        await self._view.player.set_volume(value)
        return await interaction.response.edit_message(**self._view.build_message)


class TrackDisambiguatorView(discord.ui.View, Generic[T]):
    message: discord.Message
    selected: T

    def __init__(self, tracks: List[T]):
        super().__init__(timeout=100.0)
        self.tracks = tracks
        self.value = None

        # Use list comprehension for creating options
        options = [
            discord.SelectOption(
                label=truncate(x.title, 100),
                description='by ' + truncate(discord.utils.remove_markdown(x.author), 100),
                emoji=to_emoji(i),
                value=str(i)
            )
            for i, x in enumerate(tracks)
        ]

        select = discord.ui.Select(options=options)
        select.callback = self.on_select_submit
        self.select = select
        self.add_item(select)

    async def on_select_submit(self, interaction: discord.Interaction) -> None:  # noqa
        index = int(self.select.values[0])
        self.selected = self.tracks[index]
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa
        self.selected = None
        self.stop()

    @classmethod
    async def start(
            cls: Type[TrackDisambiguatorView],
            context: Context | discord.Interaction,
            *,
            tracks: List[T]
    ) -> Optional[TrackDisambiguatorView[T]]:
        """|coro|

        Used to start the disambiguator."""
        tracks = tracks[:5]

        if len(tracks) == 1:
            return tracks[0]

        if len(tracks) == 0:
            return None

        self = cls(tracks=tracks)
        self.ctx = context

        description = '\n'.join(
            f'{to_emoji(i)} [{track.title}]({track.uri}) by **{track.author}** | `{converters.convert_duration(track.length)}`'
            for i, track in enumerate(tracks)
        )

        embed = discord.Embed(
            title='Choose a Track',
            description=description,
            timestamp=datetime.datetime.now(datetime.UTC),
            color=helpers.Colour.teal())
        embed.set_footer(text=context.user, icon_url=context.user.avatar.url)

        self.message = await context.send(embed=embed, view=self)

        await self.wait()
        try:
            await self.message.delete()
        except discord.HTTPException:
            pass

        return self.selected
