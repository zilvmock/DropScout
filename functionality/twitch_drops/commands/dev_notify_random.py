from __future__ import annotations

import random

import lightbulb

from ..differ import DropsDiff
from ..notifier import DropsNotifier
from .common import SharedContext


def register(client: lightbulb.Client, shared: SharedContext) -> str:
    @client.register
    class DropsNotifyRandom(
        lightbulb.SlashCommand,
        name="drops_notify_random",
        description="Dev-only: trigger notifier for a random ACTIVE campaign",
    ):
        @lightbulb.invoke
        async def invoke(self, ctx: lightbulb.Context) -> None:
            try:
                await ctx.defer(ephemeral=True)
            except Exception:
                pass
            recs = await shared.get_campaigns_cached()
            active = [r for r in recs if r.status == "ACTIVE"]
            if not active:
                await ctx.respond("No ACTIVE campaigns available to notify.", ephemeral=True)
                return
            r = random.choice(active)
            notifier = DropsNotifier(
                ctx.client.app,
                shared.guild_store,
                shared.favorites_store,
                shared.game_catalog,
            )
            try:
                await notifier.notify(DropsDiff(activated=[r]))
                await ctx.respond(
                    f"Triggered notifier for: {(r.game_name or r.name or r.id)}.",
                    ephemeral=True,
                )
            except Exception:
                await ctx.respond("Failed to trigger notifier.", ephemeral=True)

    return "drops_notify_random"
