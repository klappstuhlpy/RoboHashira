from typing import Any, Optional
from discord.ext.commands import UserInputError


class BadArgument(UserInputError):
    """Custom Class with added functionality for prefix.

    Exception raised when a parsing or conversion failure is encountered
    on an argument to pass into a command.

    This inherits from :exc:`UserInputError`
    """

    def __init__(self, message: Optional[str] = None, *args: Any) -> None:
        if message is not None:
            # clean-up @everyone and @here mentions
            m = message.replace('@everyone', '@\u200beveryone').replace('@here', '@\u200bhere')
            # Add a Tick Emoji to the message
            super().__init__(f"<:redTick:1079249771975413910> {m}", *args)
        else:
            super().__init__(*args)
