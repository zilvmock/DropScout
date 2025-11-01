from __future__ import annotations

"""Shared helpers and context for DropScout slash commands."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, cast

import asyncio
import hikari
from hikari.files import Bytes, Resourceish

from ..config import GuildConfigStore
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

    async def finalize_interaction(self, ctx: Any, *, message: Optional[str] = None) -> None:
        """Clear or update the deferred 'thinking…' placeholder if present."""
        # Try delete last/initial response first to avoid clutter
        fn = self._get_async(ctx, "delete_last_response")
        if fn is not None:
            try:
                await fn()
                return
            except Exception:
                pass
        fn = self._get_async(ctx, "delete_initial_response")
        if fn is not None:
            try:
                await fn()
                return
            except Exception:
                pass
        # Fall back to editing the placeholder
        content = message if (message is not None and message != "") else "Done."
        fn = self._get_async(ctx, "edit_last_response")
        if fn is not None:
            try:
                await fn(content=content)
                return
            except Exception:
                pass
        fn = self._get_async(ctx, "edit_initial_response")
        if fn is not None:
            try:
                await fn(content=content)
                return
            except Exception:
                pass
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
