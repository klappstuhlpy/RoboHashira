import re
from pathlib import Path
from typing import Callable, Dict, Any, TypeVar, Coroutine

BOT_BASE_FOLDER = Path(__file__).parent.parent.parent.absolute()

ObjectHook = Callable[[Dict[str, Any]], Any]

Coro = TypeVar('Coro', bound=Callable[..., Coroutine[Any, Any, Any]])

REVISION_FILE = re.compile(r'(?P<kind>V|U)(?P<version>[0-9]+)__(?P<description>.+).sql')
