from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import hikari
from hikari.files import Bytes, Resourceish
import lightbulb

from ..embeds import build_campaign_embed
from ..images import build_benefits_collage
from ..models import CampaignRecord
from .common import SharedContext


def register(client: lightbulb.Client, shared: SharedContext) -> str:
    @client.register
    class DropsActive(
        lightbulb.SlashCommand,
        name="drops_active",
        description="List campaigns that are currently ACTIVE",
    ):
        @lightbulb.invoke
        async def invoke(self, ctx: lightbulb.Context) -> None:
            try:
                await ctx.defer()
            except Exception:
                pass
            recs = await shared.get_campaigns_cached()
            now = int(datetime.now(timezone.utc).timestamp())
            active = [r for r in recs if r.status == "ACTIVE"]
            active.sort(key=lambda r: (r.ends_ts or (now + 10**10)))
            embeds: List[hikari.Embed] = []
            attach_aligned: List[Resourceish | None] = []
            attaches_done = 0
            for r in active:
                e = build_campaign_embed(r, title_prefix="Active Campaign")
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

    return "drops_active"

