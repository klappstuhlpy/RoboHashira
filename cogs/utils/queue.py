from dataclasses import dataclass
from typing import List, Union

import discord
import wavelink
from discord.utils import MISSING
from cogs.utils import formats


@dataclass(frozen=True)
class ListenTogether:
    enabled: bool
    user_id: int


class Queue(wavelink.Queue):
    """A custom Queue class for the Player class."""

    def __init__(self):
        super().__init__()
        self._listen_together: ListenTogether = ListenTogether(enabled=False, user_id=MISSING)
        self._shuffle: bool = False

    @property
    def all(self) -> List[wavelink.Playable]:
        """Returns a list of all tracks in the queue and history without duplicates."""
        return list(formats.merge(self.history, self._queue))

    @property
    def duration(self) -> int:
        """Returns the total duration of the queue and history."""
        return sum(track.length for track in self.all if track is not self._loaded)

    @property
    def history_is_empty(self) -> bool:
        """Returns True if the history has no members."""
        return not bool((len(self.history) - 1) if len(self.history) > 0 else 0)

    @property
    def all_is_empty(self) -> bool:
        """Returns True if the queue + history has no members."""
        return not bool(self.all)

    @property
    def future_is_empty(self) -> bool:
        """Returns True if the queue has no members."""
        return not bool(self._queue)

    @property
    def shuffle(self) -> bool:
        """Returns the current shuffle state."""
        return self._shuffle

    @property
    def listen_together(self) -> ListenTogether:
        """Returns the current listen together state."""
        return self._listen_together

    @listen_together.setter
    def listen_together(self, value: Union[bool, int]):
        """Sets the listen together state."""
        if not isinstance(value, dict):
            raise ValueError('The "listen_together" property can only be set with a dict.')
        self._listen_together = ListenTogether(**value)  # noqa

    @shuffle.setter
    def shuffle(self, value: bool):
        """Sets the shuffle state."""
        if not isinstance(value, bool):
            raise ValueError('The "shuffle" property can only be set with a bool.')
        self._shuffle = value

    def put_at_index(self, index: int, item: wavelink.Playable) -> None:
        """Put the given item into the queue at the specified index."""
        self._queue.insert(index, item)

    def put_at_front(self, item: wavelink.Playable) -> None:
        """Put the given item into the front of the queue."""
        self.put_at_index(0, item)
