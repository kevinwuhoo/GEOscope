"""Consistent dotenv bootstrap for every GEOscope backend entry point."""

from __future__ import annotations

from dotenv import find_dotenv, load_dotenv


_DOTENV_FILES = (".env.elasticsearch", ".env")


def load_environment() -> None:
    """Fill missing process variables from the nearest local dotenv files."""

    for filename in _DOTENV_FILES:
        path = find_dotenv(filename, usecwd=True)
        if path:
            load_dotenv(path, override=False)
