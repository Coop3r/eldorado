import fcntl
import logging
import sys

from .bot import EldoBot
from .config import ConfigError, load_settings


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request at INFO, which drowns the log at one poll a minute.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc)
        return 1
    # Two copies would double every alert, so refuse to start a second one.
    lock = open("eldobot.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("eldobot is already running from this folder. Stop it first: pkill -f 'm eldobot'")
        return 1
    EldoBot(settings).run(settings.discord_token, log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
