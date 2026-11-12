"""Shared logging setup for the CLI, the web UI, and the jobs.

The app's own messages log at INFO. Other packages (httpx, OpenAI, Chroma) also log at INFO by
default, and their request logs print endpoint URLs. So at INFO, records from other packages are
dropped by a handler filter. Warnings and errors from every package still show.

The filter allows only our own modules instead of blocking known library names, because library
logger names change between releases (a newer httpx logs as "httpx2").

Set LOG_LEVEL=DEBUG in the environment or .env to see everything, including those request lines
and environment IDs.
"""
import logging
import os
from pathlib import Path

# Kept as a second layer: it stops these loggers before they build a record.
_LIBRARY_LOGGERS = ("httpx", "httpcore", "openai", "chromadb", "urllib3")

# Loggers that belong to this project: the root logger (the jobs call logging.info directly),
# a script run as __main__, and every module in the project folder.
_OWN_LOGGERS = {"root", "__main__"} | {p.stem for p in Path(__file__).resolve().parent.glob("*.py")}


class _OwnInfoOnly(logging.Filter):
    """Pass WARNING and above from anywhere. Below that, pass only this project's own records."""

    def filter(self, record):
        if record.levelno >= logging.WARNING:
            return True
        return record.name.split(".")[0] in _OWN_LOGGERS


def configure_logging():
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
    debugging = level <= logging.DEBUG
    # force=True replaces any handler a library installed first.
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s", force=True)

    if not debugging:
        for handler in logging.getLogger().handlers:
            handler.addFilter(_OwnInfoOnly())
    library_level = logging.DEBUG if debugging else logging.WARNING
    for name in _LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(library_level)
