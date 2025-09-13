from __future__ import annotations

import lightbulb

from .common import SharedContext


def register(client: lightbulb.Client, shared: SharedContext) -> str:
    @client.register
    class DropsChannel(
        lightbulb.SlashCommand,
        name="drops_channel",
        description="Show the current notifications channel for this server",
    ):
        @lightbulb.invoke
        async def invoke(self, ctx: lightbulb.Context) -> None:
            if not ctx.guild_id:
                await ctx.respond("This command must be used in a server.", ephemeral=True)
                return
            gid = int(ctx.guild_id)
            configured = shared.guild_store.get_channel_id(gid)
            if configured:
                await ctx.respond(f"Notifications channel is <#{configured}>.")
                return
            try:
                g = await ctx.client.app.rest.fetch_guild(gid)
                scid = getattr(g, "system_channel_id", None)
                if scid:
                    await ctx.respond(
                        f"No channel configured. Defaults to system channel <#{int(scid)}>.")
                else:
                    await ctx.respond(
                        "No channel configured and no system channel set. Use /drops_set_channel.",
                        ephemeral=True,
                    )
            except Exception:
                await ctx.respond(
                    "No channel configured. Use /drops_set_channel to set one.",
                    ephemeral=True,
                )

    return "drops_channel"

