from __future__ import annotations

"""Shared helpers and context for DropScout slash commands."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, cast

import asyncio
import hikari
from hikari.files import Bytes, Resourceish

from ..config import GuildConfigStore
from ..favorites import FavoritesStore
from ..game_catalog import GameCatalog
from ..models import CampaignRecord


@dataclass
class SharedContext:
    """Holds shared configuration, caches, and helpers for commands."""

    guild_store: GuildConfigStore
    ICON_LIMIT: int
    ICON_SIZE: int
    ICON_COLUMNS: int
    MAX_ATTACH_PER_CMD: int
    SEND_DELAY_MS: int
    FETCH_TTL: int
    game_catalog: GameCatalog
    favorites_store: FavoritesStore

    _cache_data: list[CampaignRecord] = field(default_factory=list)
    _cache_exp: float = 0.0

    async def get_campaigns_cached(self) -> list[CampaignRecord]:
        now_ts = datetime.now(timezone.utc).timestamp()
        if self._cache_data and now_ts < self._cache_exp:
            return self._cache_data
        # Import module at call time so tests can monkeypatch DropsFetcher
        from .. import fetcher as fetcher_mod
        fetcher = fetcher_mod.DropsFetcher()
        data = await fetcher.fetch_condensed()
        self._cache_data = data
        self._cache_exp = now_ts + self.FETCH_TTL
        try:
            self.game_catalog.merge_from_campaign_records(data)
        except Exception:
            pass
        return data

    def _get_async(self, ctx: Any, name: str) -> Optional[Callable[..., Awaitable[Any]]]:
        fn = getattr(ctx, name, None)
        if fn is None or not callable(fn):
            return None
        return cast(Callable[..., Awaitable[Any]], fn)

    def _was_deferred(self, ctx: Any) -> bool:
        """Best-effort detection that an interaction was previously deferred."""
        for attr in ("_dropscout_deferred", "deferred", "_deferred"):
            val = getattr(ctx, attr, None)
            if isinstance(val, bool):
                if val:
                    return True
            elif val is not None and not callable(val):
                try:
                    if bool(val):
                        return True
                except Exception:
                    continue
        for attr in ("is_deferred",):
            val = getattr(ctx, attr, None)
            if callable(val):
                try:
                    result = val()
                except Exception:
                    continue
                else:
                    if isinstance(result, bool) and result:
                        return True
            elif isinstance(val, bool) and val:
                return True
        interaction = getattr(ctx, "interaction", None)
        if interaction is None:
            return False
        for attr in ("is_deferred", "has_responded"):
            val = getattr(interaction, attr, None)
            if callable(val):
                try:
                    result = val()
                except Exception:
                    continue
                else:
                    if isinstance(result, bool) and result:
                        return True
            elif isinstance(val, bool) and val:
                return True
        return False

    async def finalize_interaction(self, ctx: Any, *, message: Optional[str] = None) -> None:
        """Clear or update the deferred 'thinking…' placeholder if present."""
        content = message if (message is not None and message != "") else "Done."
        notify = bool(message not in (None, "")) or self._was_deferred(ctx)

        async def _run(name: str, **kwargs: Any) -> bool:
            fn = self._get_async(ctx, name)
            if fn is None:
                return False
            try:
                await fn(**kwargs)
            except Exception:
                return False
            return True

        if notify:
            if await _run("edit_last_response", content=content):
                return
            if await _run("edit_initial_response", content=content):
                return
            await _run("delete_last_response")
            await _run("delete_initial_response")
            try:
                await ctx.respond(content, ephemeral=True)
            except Exception:
                pass
            return

        if await _run("delete_last_response"):
            return
        if await _run("delete_initial_response"):
            return
        if await _run("edit_last_response", content=content):
            return
        if await _run("edit_initial_response", content=content):
            return
        # Absolute last resort: ephemeral follow-up note
        try:
            await ctx.respond(content, ephemeral=True)
        except Exception:
            pass

    async def send_embeds(
        self,
        ctx: Any,
        embeds: list[hikari.Embed],
        attachments_aligned: list[Resourceish | None] | None = None,
    ) -> None:
        """Send embeds, handling attachments reliably.

        If attachments_aligned is provided and contains any items, sends each
        embed as its own message with its corresponding attachment to ensure
        correct filename->embed mapping. Otherwise, sends in chunks of up to 10
        embeds per message for efficiency.
        """
        if not embeds:
            await ctx.respond("No campaigns found.")
            return
        if attachments_aligned and any(a is not None for a in attachments_aligned):
            for e, a in zip(embeds, attachments_aligned):
                if a is not None and isinstance(a, Bytes):
                    e.set_image(a)
                await ctx.client.app.rest.create_message(ctx.channel_id, embeds=[e])
                await asyncio.sleep(self.SEND_DELAY_MS / 1000)
            return
        # No attachments: chunk efficiently
        for i in range(0, len(embeds), 10):
            chunk = embeds[i : i + 10]
            await ctx.respond(embeds=chunk)


def mark_deferred(ctx: Any) -> None:
    """Mark a context as deferred so finalize_interaction knows to clean up."""
    try:
        setattr(ctx, "_dropscout_deferred", True)
    except Exception:
        pass
