from __future__ import annotations

"""
Legacy `/drops_this_week` command module.

The command is benched and no longer registered by default, but remains in the
codebase for potential reactivation.
"""

from datetime import datetime, timezone, timedelta
from typing import List

import hikari
from hikari.files import Bytes, Resourceish
import lightbulb

from ..embeds import build_campaign_embed
from ..images import build_benefits_collage
from .common import SharedContext, mark_deferred


def register(client: lightbulb.Client, shared: SharedContext) -> str:
    @client.register
    class DropsThisWeek(
        lightbulb.SlashCommand,
        name="drops_this_week",
        description="Show ACTIVE campaigns ending this week",
    ):
        @lightbulb.invoke
        async def invoke(self, ctx: lightbulb.Context) -> None:
            try:
                await ctx.defer()
            except Exception:
                pass
            else:
                mark_deferred(ctx)
            recs = await shared.get_campaigns_cached()
            now = datetime.now(timezone.utc)
            weekday = now.weekday()  # Monday=0
            days_ahead = (7 - weekday) or 7
            next_monday = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=days_ahead)
            horizon_ts = int(next_monday.timestamp())
            active_week = [r for r in recs if r.status == "ACTIVE" and (r.ends_ts or 0) <= horizon_ts]
            active_week.sort(key=lambda r: r.ends_ts or horizon_ts)
            embeds: List[hikari.Embed] = []
            attach_aligned: List[Resourceish | None] = []
            attaches_done = 0
            for r in active_week:
                e = build_campaign_embed(r, title_prefix="Active This Week")
                png, fname = (None, None)
                if shared.MAX_ATTACH_PER_CMD <= 0 or attaches_done < shared.MAX_ATTACH_PER_CMD:
                    png, fname = await build_benefits_collage(
                        r,
                        limit=shared.ICON_LIMIT if shared.ICON_LIMIT >= 0 else 9,
                        icon_size=(shared.ICON_SIZE, shared.ICON_SIZE),
                        columns=shared.ICON_COLUMNS,
                    )
                if png and fname:
                    attach_aligned.append(Bytes(png, fname))
                    attaches_done += 1
                else:
                    if r.benefits and r.benefits[0].image_url:
                        e.set_image(r.benefits[0].image_url)  # type: ignore[arg-type]
                    attach_aligned.append(None)
                embeds.append(e)
            await shared.send_embeds(ctx, embeds, attach_aligned)
            await shared.finalize_interaction(ctx)

    return "drops_this_week"
