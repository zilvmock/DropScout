from __future__ import annotations

"""Command registration package for DropScout.

Exposes a single `register_commands(client)` that sets up all commands using
separate modules. Development-only commands are included only in non-prod.
"""

import os
from typing import List

import lightbulb

from ..config import GuildConfigStore
from .common import SharedContext


def _is_production() -> bool:
    env = (os.getenv("ENV") or os.getenv("DROPSCOUT_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    return (
        (os.getenv("PRODUCTION") or os.getenv("IS_PRODUCTION") or "false").strip().lower() == "true"
        or env in ("prod", "production")
    )


def register_commands(client: lightbulb.Client) -> List[str]:
    """Register all DropScout commands on a Lightbulb client and return names."""
    GUILD_STORE_PATH = os.getenv("TWITCH_GUILD_STORE_PATH", "data/guild_config.json")
    guild_store = GuildConfigStore(GUILD_STORE_PATH)

    ICON_LIMIT = int(os.getenv("DROPS_ICON_LIMIT", "9") or 9)
    ICON_SIZE = int(os.getenv("DROPS_ICON_SIZE", "96") or 96)
    ICON_COLUMNS = int(os.getenv("DROPS_ICON_COLUMNS", "3") or 3)
    MAX_ATTACH_PER_CMD = int(os.getenv("DROPS_MAX_ATTACHMENTS_PER_CMD", "0") or 0)
    SEND_DELAY_MS = int(os.getenv("DROPS_SEND_DELAY_MS", "350") or 350)
    FETCH_TTL = int(os.getenv("DROPS_FETCH_TTL_SECONDS", "120") or 120)

    shared = SharedContext(
        guild_store=guild_store,
        ICON_LIMIT=ICON_LIMIT,
        ICON_SIZE=ICON_SIZE,
        ICON_COLUMNS=ICON_COLUMNS,
        MAX_ATTACH_PER_CMD=MAX_ATTACH_PER_CMD,
        SEND_DELAY_MS=SEND_DELAY_MS,
        FETCH_TTL=FETCH_TTL,
    )

    names: List[str] = []

    # Core commands
    from .hello import register as reg_hello
    from .help import register as reg_help
    from .set_channel import register as reg_set_channel
    from .channel import register as reg_channel
    from .active import register as reg_active
    from .this_week import register as reg_this_week
    from .search_game import register as reg_search_game

    names.append(reg_hello(client, shared))
    names.append(reg_help(client, shared))
    names.append(reg_active(client, shared))
    names.append(reg_this_week(client, shared))
    names.append(reg_set_channel(client, shared))
    names.append(reg_channel(client, shared))
    names.append(reg_search_game(client, shared))

    # Dev-only commands
    if not _is_production():
        from .dev_notify_random import register as reg_dev_notify

        names.append(reg_dev_notify(client, shared))

    return names

