from __future__ import annotations
from typing import Optional, List, Any, Type, cast

import asyncpg
import wavelink
from discord.ext import commands
import discord
from discord import app_commands
from discord.ext.commands._types import BotT
from wavelink import Playable

from .music import Music
from .utils.context import Context
from .utils import checks, cache, fuzzy, formats, _commands
from bot import RoboHashira
from .utils.formats import plural, get_shortened_string
from cogs.utils.player import Player
from cogs.utils.paginator import BasePaginator, TextSource


class PlaylistSelect(discord.ui.Select):
    def __init__(self, playlists: list[Playlist], paginator: PlaylistPaginator):
        self.paginator = paginator
        options = [
            discord.SelectOption(
                label='Start Page',
                emoji=discord.PartialEmoji(name='vegaleftarrow', id=1066024601332748389),
                value='__index',
                description='The front page of the Todo Menu.')]
        options.extend([playlist.to_select_option(i) for i, playlist in enumerate(playlists)])
        super().__init__(placeholder=f'Select a playlist ({len(playlists)} playlists found)',
                         options=options)

    async def callback(self, interaction: discord.Interaction) -> Any:
        index = int(self.values[0])
        if index == 0:
            self.paginator.pages = self.paginator.start_pages
        else:
            playlist = self.paginator.playlists[index - 1]
            self.paginator.pages = playlist.to_embeds()

        self.paginator._current_page = 0
        self.paginator.update_buttons()
        await interaction.response.edit_message(**self.paginator._message_kwargs(self.paginator.pages[0]))


class PlaylistPaginator(BasePaginator):
    playlists: List[Playlist]
    start_pages: List[discord.Embed]

    def __init__(self, *, entries: List, per_page: int = 10, clamp_pages: bool = True,
                 timeout: int = 180) -> None:
        super().__init__(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.add_item(PlaylistSelect(self.playlists, self))

    async def format_page(self, entries: List, /) -> discord.Embed:
        if isinstance(entries, discord.Embed):
            return entries
        return entries[0]

    @classmethod
    async def start(
            cls: Type[BasePaginator],
            context: Context | discord.Interaction,
            *,
            entries: List,
            per_page: int = 10,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False
    ) -> BasePaginator:
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        page = await self.format_page(self.pages[0])

        self.msg = await cls._send(context, ephemeral, view=self, embed=page)
        return self


class Playlist:
    def __init__(self, cog: PlaylistTools, record: asyncpg.Record):
        self.cog: PlaylistTools = cog
        self.bot: RoboHashira = cog.bot

        self.id = record['id']
        self.name = record['name']
        self.owner_id = record['user_id']
        self.created_at = record['created']
        self.tracks: list[PlaylistTrack] = []

        self.is_liked_songs = self.name == 'Liked Songs'

    def __contains__(self, item: str) -> bool:
        for track in self.tracks:
            if item == track.url:
                return True
        return False

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Playlist):
            return self.id == other.id
        return False

    def __repr__(self):
        return f'<Playlist id={self.id} name={self.name}>'

    def __str__(self):
        return self.name

    def __len__(self):
        return len(self.tracks)

    @property
    def field_tuple(self) -> tuple[str, str]:
        name = f'#{self.id}: {self.name}'
        if self.is_liked_songs:
            name = self.name

        value = None
        if len(self.tracks) >= 1:
            value = f'with {plural(len(self.tracks)):Track}'

        return name, value or '...'

    @property
    def choice_text(self) -> str:
        if self.is_liked_songs:
            return self.name
        return f'[{self.id}] {self.name}'

    async def add_track(self, track: Playable) -> PlaylistTrack:
        query = "INSERT INTO playlist_lookup (playlist_id, name, url) VALUES ($1, $2, $3) RETURNING *;"
        record = await self.bot.pool.fetchrow(query, self.id, track.title, track.uri)

        track = PlaylistTrack(record)
        self.tracks.append(track)
        return track

    async def remove_track(self, track: PlaylistTrack):
        await self.bot.pool.execute("DELETE FROM playlist_lookup WHERE id = $1;", track.id)
        self.tracks.remove(track)

    def to_embeds(self) -> List[discord.Embed]:
        source = TextSource(prefix=None, suffix=None, max_size=3080)
        if len(self.tracks) == 0:
            source.add_line('*This playlist is empty.*')
        else:
            for index, track in enumerate(self.tracks):
                source.add_line(f'`{index + 1}.` {track.text}')

        embeds = []
        for page in source.pages:
            embed = discord.Embed(title=f'{self.name} ({plural(len(self.tracks)):Track})',
                                  timestamp=self.created_at,
                                  description=page)
            embed.set_footer(text=f'[{self.id}] • Created at')
            embeds.append(embed)

        return embeds

    def to_select_option(self, value: Any) -> discord.SelectOption:
        return discord.SelectOption(
            label=self.name,
            emoji='\N{MULTIPLE MUSICAL NOTES}',
            value=str(value),
            description=f'{len(self.tracks)} Tracks')

    async def delete(self) -> None:
        query = "DELETE FROM playlist WHERE id = $1;"
        await self.bot.pool.execute(query, self.id)

        query = "DELETE FROM playlist_lookup WHERE playlist_id = $1;"
        await self.bot.pool.execute(query, self.id)

        self.cog.get_playlists.invalidate(self, self.owner_id)

    async def clear(self) -> None:
        query = "DELETE FROM playlist_lookup WHERE playlist_id = $1;"
        await self.bot.pool.execute(query, self.id)

        self.tracks = []


class PlaylistTrack:
    def __init__(self, record: asyncpg.Record):
        self.id = record['id']
        self.name = record['name']
        self.url = record['url']

    @property
    def text(self) -> str:
        return f'[{self.name}]({self.url}) (ID: {self.id})'


class PlaylistTools(commands.Cog):
    """Additional Music Tools for the Music Cog.
    Like: Playlist, DJ, Setup etc."""

    def __init__(self, bot):
        self.bot: RoboHashira = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='staff_animated', id=1076911514193231974)

    async def cog_before_invoke(self, ctx: Context[BotT]) -> None:
        await self.initizalize_user(ctx.author)

    async def initizalize_user(self, user: discord.abc.User | discord.Member) -> int | None:
        # Creates a static Playlist for every new User that interacts with the Bot
        # called 'Liked Songs', this Playlist cannot be deleted
        # and is used to store all liked songs from the user.

        # The User can store Liked Songs using the Button the Player Control Panel

        if playlists := await self.get_playlists(user.id):
            if any(playlist.is_liked_songs for playlist in playlists):
                return None

        record = await self.bot.pool.fetchval(
            "INSERT INTO playlist (user_id, name, created) VALUES ($1, $2, $3) RETURNING id;",
            user.id, 'Liked Songs', discord.utils.utcnow().replace(tzinfo=None))
        self.get_playlists.invalidate(self, user.id)
        return record

    async def playlist_id_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        playlists = await self.get_playlists(interaction.user.id)
        results = fuzzy.finder(current, playlists, key=lambda p: p.choice_text, raw=True)

        if interaction.command.name == 'delete':
            # Remove the Liked Songs Playlist from the results
            # because it must not be deleted
            results.remove(discord.utils.get(results, name='Liked Songs'))

        return [
            app_commands.Choice(name=get_shortened_string(length, start, playlist.choice_text), value=playlist.id)
            for length, start, playlist in results[:20]]

    async def get_playlist(self, playlist_id: int, pass_tracks: bool = False) -> Optional[Playlist]:
        """Gets a poll by ID."""
        record = await self.bot.pool.fetchrow("SELECT * FROM playlist WHERE id=$1;", playlist_id)
        playlist = Playlist(self, record) if record else None
        if playlist is not None and pass_tracks is False:
            records = await self.bot.pool.fetch("SELECT * FROM playlist_lookup WHERE playlist_id=$1;", playlist_id)
            playlist.tracks = [PlaylistTrack(record) for record in records]
        return playlist

    async def get_liked_songs(self, user_id: int) -> Optional[Playlist]:
        """Gets a User 'Liked Songs' playlist."""
        record = await self.bot.pool.fetchrow("SELECT * FROM playlist WHERE user_id=$1 AND name=$2 LIMIT 1;", user_id,
                                              'Liked Songs')
        playlist = Playlist(self, record) if record else None
        if playlist is not None:
            records = await self.bot.pool.fetch("SELECT * FROM playlist_lookup WHERE playlist_id=$1;", playlist.id)
            playlist.tracks = [PlaylistTrack(record) for record in records]
        return playlist

    @cache.cache()
    async def get_playlists(self, user_id: int) -> list[Playlist]:
        """Get all playlists from a user."""
        records = await self.bot.pool.fetch("SELECT * FROM playlist WHERE user_id=$1;", user_id)
        playlists = [Playlist(self, record) for record in records]
        for playlist in playlists:
            records = await self.bot.pool.fetch("SELECT * FROM playlist_lookup WHERE playlist_id=$1;", playlist.id)
            playlist.tracks = [PlaylistTrack(record) for record in records]
        return playlists

    @_commands.command(
        commands.hybrid_group,
        name='playlist',
        description='Manage your playlist.'
    )
    async def playlist(self, ctx: Context):
        """Manage your playlist."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_commands.command(playlist.command, name='list', description='Display all your playlists and tracks.')
    async def playlist_list(self, ctx: Context):
        """Display all your playlists and tracks."""
        playlists = await self.get_playlists(ctx.author.id)
        if not playlists:
            return await ctx.stick(
                False, f'You don\'t have any playlists. You can create a playlist using `{ctx.prefix}playlist create`.',
                ephemeral=True)

        items = [playlist.field_tuple for playlist in playlists]

        fields = []
        for i in range(0, len(items), 12):
            fields.append(items[i:i + 12])

        embeds = []
        for index, field in enumerate(fields):
            embed = discord.Embed(
                title='Your Playlists',
                description='Here are your playlists, use the buttons and view to navigate',
                color=self.bot.colour.teal())
            embed.set_author(name=ctx.author, icon_url=ctx.author.avatar.url)
            embed.set_footer(text=f'{plural(len(playlists)):playlist}')
            for name, value in field[index:index + 12]:
                embed.add_field(name=name, value=value, inline=False)
            embeds.append(embed)

        PlaylistPaginator.playlists = playlists
        PlaylistPaginator.start_pages = embeds
        await PlaylistPaginator.start(ctx, entries=embeds, per_page=1, ephemeral=True)

    @_commands.command(playlist.command, name='create', description='Create a new playlist.')
    @app_commands.describe(name='The name of your new playlist.')
    async def playlist_create(self, ctx: Context, name: str):
        """Create a new playlist."""
        playlists = await self.get_playlists(ctx.author.id)

        if len(playlists) == 3 and not await self.bot.is_owner(ctx.author):
            await ctx.stick(False, 'You can only have `3` playlists at the same time.',
                            ephemeral=True)
            return

        if any(playlist.name == name for playlist in playlists):
            await ctx.stick(False, 'There is already a playlist with this name, please choose another name.',
                            ephemeral=True)
            return

        record = await self.bot.pool.fetchval(
            "INSERT INTO playlist (user_id, name, created) VALUES ($1, $2, $3) RETURNING id;",
            ctx.author.id, name, discord.utils.utcnow())
        self.get_playlists.invalidate(self, ctx.author.id)

        await ctx.stick(True, f'Successfully created playlist **{name}** [`{record}`].',
                        ephemeral=True)

    @_commands.command(
        playlist.command,
        name='play',
        description='Add the songs from you playlist to the plugins queue and play them.',
        guild_only=True
    )
    @app_commands.describe(playlist_id='The ID of your playlist to play.')
    @app_commands.autocomplete(playlist_id=playlist_id_autocomplete)  # type: ignore
    @checks.is_listen_together()
    @checks.is_author_connected()
    async def playlist_play(self, ctx: Context, playlist_id: int):
        """Add the songs from you playlist to the plugins queue and play them."""
        player: Player = cast(Player, ctx.voice_client)

        if not player:
            music: Music = self.bot.get_cog('Music')  # type: ignore
            player = await music.join(ctx)

        playlist = await self.get_playlist(playlist_id)
        if playlist is None:
            await ctx.stick(False, 'There is no playlist with this id.',
                            ephemeral=True)
            return

        if len(playlist) == 0:
            await ctx.stick(False, 'There are no tracks in this playlist, please add some using `/playlist add`.',
                            ephemeral=True)
            return

        old_stamp = len(player.queue.all) if not None else 0

        wait_message = await ctx.send(
            f'*<a:loading:1072682806360166430> adding tracks from your playlist to the queue... please wait...*')

        for track in playlist.tracks:
            track = await player.search(track.url)
            if not track:
                continue
            setattr(track, 'requester', ctx.author)
            await player.queue.put_wait(track)

        new_queue = len(player.queue.all) - old_stamp
        succeeded = bool(new_queue == len(playlist.tracks))

        embed = discord.Embed(
            description=f'`🎶` Successfully added **{new_queue}/{len(playlist.tracks)}** tracks from your playlist to the queue.',
            color=formats.Colour.teal())
        if not succeeded:
            embed.description += f'\n<:warning:1076913452775383080> *Some tracks may not have been added due to unexpected issues.*'
        embed.set_author(name=f'[{playlist.id}] • {playlist.name}', icon_url=ctx.author.avatar.url)
        embed.set_footer(text='Now Playing')
        await wait_message.delete()
        await ctx.send(embed=embed, delete_after=15)

        if not player.playing:
            player.autoplay = wavelink.AutoPlayMode.enabled
            await player.play(player.queue.get(), volume=70)
        else:
            await player.view.update()

    @_commands.command(
        playlist.command,
        name='add',
        description='Adds the current playing track or a track via a direct-url to your playlist.'
    )
    @app_commands.describe(query='The direct-url of the track/playlist/album you want to add to your playlist.',
                           playlist_id='The ID of your playlist.')
    @app_commands.autocomplete(playlist_id=playlist_id_autocomplete)  # type: ignore
    async def playlist_add(self, ctx: Context, playlist_id: int, *, query: Optional[str] = None):
        """Adds the current playing track or a track via a direct-url to your playlist."""
        if not query and not (ctx.voice_client and ctx.voice_client.channel):
            await ctx.stick(False, 'You have to provide either the `link` parameter or a current playing track.',
                            ephemeral=True)
            return

        playlist = await self.get_playlist(playlist_id)
        if playlist is None:
            await ctx.stick(False, 'There is no playlist with this name.', ephemeral=True)
            return

        if not query and ctx.guild.voice_client:
            player: Player = cast(Player, ctx.voice_client)

            if not player.current:
                await ctx.stick(False, 'You have to provide either the `link` parameter or a current playing track.',
                                ephemeral=True)
                return

            await playlist.add_track(player.current)
            embed = discord.Embed(
                description=f'Added Track **[{player.current.title}]({player.current.uri})** to your playlist '
                            f'at Position **#{len(playlist.tracks)}**',
                color=formats.Colour.teal()
            )
            embed.set_thumbnail(url=player.current.artwork)
            embed.set_author(name=ctx.author, icon_url=ctx.author.avatar.url)
            embed.set_footer(text=f'[{playlist.id}] • {playlist.name}')
            await ctx.send(embed=embed, ephemeral=True)
        else:
            temp_player = Player(self.bot)

            query = query.strip('<>')
            result = await temp_player.search(query, wavelink.TrackSource.YouTubeMusic, ctx)

            if result is None:
                await ctx.stick(False, 'Sorry! No results found matching your query.',
                                ephemeral=True, delete_after=10)
                return

            if await temp_player.check_blacklist(result):
                await ctx.stick(False, 'Blacklisted track detected. Please try another one.',
                                ephemeral=True, delete_after=10)
                return

            added = [track.url for track in playlist.tracks]
            if isinstance(result, wavelink.Playlist):

                success = 0
                for track in result.tracks:
                    if track.uri in added:
                        continue
                    await playlist.add_track(track)
                    success += 1

                embed = discord.Embed(
                    description=f'Added **{success}**/**{len(result.tracks)}** Tracks from {result.name} **[{result.name}]({result.url})** to your playlist.\n'
                                f'Next Track at Position **#{len(playlist.tracks)}**',
                    color=formats.Colour.teal())
                embed.set_thumbnail(url=result.artwork)
                embed.set_author(name=ctx.author, icon_url=ctx.author.avatar.url)
                embed.set_footer(text=f'[{playlist.id}] • {playlist.name}')
                await ctx.send(embed=embed, ephemeral=True)
            else:
                if result.uri in added:
                    await ctx.stick(False, 'This Track is already in your playlist.',
                                    ephemeral=True, delete_after=10)
                    return

                await playlist.add_track(result)

                embed = discord.Embed(
                    description=f'Added Track **[{result.title}]({result.uri})** to your playlist.\n'
                                f'Track at Position **#{len(playlist.tracks)}**',
                    color=formats.Colour.teal())
                embed.set_thumbnail(url=result.artwork)
                embed.set_author(name=ctx.author, icon_url=ctx.author.avatar.url)
                embed.set_footer(text=f'[{playlist.id}] • {playlist.name}')
                await ctx.send(embed=embed, ephemeral=True)

        self.get_playlists.invalidate(self, ctx.author.id)

    @_commands.command(
        playlist.command,
        name='delete',
        description='Delete a playlist.'
    )
    @app_commands.describe(playlist_id='The ID of the playlist you want to delete.')
    @app_commands.autocomplete(playlist_id=playlist_id_autocomplete)  # type: ignore
    async def playlist_delete(self, ctx: Context, playlist_id: int):
        """Delete a playlist."""
        playlist = await self.get_playlist(playlist_id, pass_tracks=True)
        if playlist is None:
            await ctx.stick(False, 'No playlist found matching your query.',
                            ephemeral=True)
            return

        await playlist.delete()
        await ctx.stick(True, 'Successfully deleted playlist **{playlist.name}** [`{playlist.id}`] '
                              f'and all corresponding entries.',
                        ephemeral=True)
        self.get_playlists.invalidate(self, ctx.author.id)

    @_commands.command(
        playlist.command,
        name='clear',
        description='Clear all Items in a playlist.'
    )
    @app_commands.describe(playlist_id='The ID of the playlist you want to delete.')
    @app_commands.autocomplete(playlist_id=playlist_id_autocomplete)  # type: ignore
    async def playlist_clear(self, ctx: Context, playlist_id: int):
        """Clear all Items in a playlist."""
        playlist = await self.get_playlist(playlist_id, pass_tracks=True)
        if playlist is None:
            await ctx.stick(False, 'No playlist found matching your query.',
                            ephemeral=True)
            return

        await playlist.clear()
        await ctx.stick(True, 'Successfully purged all corresponding entries of '
                              f'playlist **{playlist.name}** [`{playlist.id}`].',
                        ephemeral=True)
        self.get_playlists.invalidate(self, ctx.author.id)

    @_commands.command(
        playlist.command,
        name='remove',
        description='Remove a track from your playlist.'
    )
    @app_commands.describe(playlist_id='The playlist ID you want to remove a track from.',
                           track_id='The ID of the track to remove.')
    @app_commands.autocomplete(playlist_id=playlist_id_autocomplete)  # type: ignore
    async def playlist_remove(self, ctx: Context, playlist_id: int, track_id: int):
        """Remove a track from your playlist."""
        playlist = await self.get_playlist(playlist_id)
        if playlist is None:
            await ctx.stick(False, 'No playlist found matching your query.',
                            ephemeral=True)
            return

        track = discord.utils.get(playlist.tracks, id=track_id)
        if not track:
            await ctx.stick(False, 'No track found matching your query.',
                            ephemeral=True)
            return

        await playlist.remove_track(track)
        await ctx.stick(True, 'Successfully removed track **{track.name}** [`{track.id}`] '
                              f'from playlist **{playlist.name}** [`{playlist.id}`].',
                        ephemeral=True)


async def setup(bot):
    await bot.add_cog(PlaylistTools(bot))
