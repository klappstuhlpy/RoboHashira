import re
from pathlib import Path
from typing import Callable, Dict, Any, TypeVar, Coroutine

BOT_BASE_FOLDER = Path(__file__).parent.parent.parent.as_posix()

ObjectHook = Callable[[Dict[str, Any]], Any]

Coro = TypeVar('Coro', bound=Callable[..., Coroutine[Any, Any, Any]])
NonCoro = TypeVar('NonCoro', bound=Callable[..., Any])

REVISION_FILE = re.compile(r'(?P<kind>[VU])(?P<version>[0-9]+)__(?P<description>.+).sql')
INVITE_REGEX = re.compile(r'(?:https?:)?discord(?:\.gg|\.com|app\.com(/invite)?)?[A-Za-z0-9]+')
MENTION_REGEX = re.compile(r'<@(!?)([0-9]*)>')
URL_REGEX = re.compile(r'https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|%[0-9a-fA-F][0-9a-fA-F])+')
VOLUME_REGEX = re.compile(r'^[+-]?\d+$')

defaultBands = [{'band': i, 'gain': 0.0} for i in range(15)]
