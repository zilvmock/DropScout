from __future__ import annotations

import re
import lightbulb
from lightbulb.commands import options as opt

from .common import SharedContext


def register(client: lightbulb.Client, shared: SharedContext) -> str:
    @client.register
    class SetNotifyChannel(
        lightbulb.SlashCommand,
        name="drops_set_channel",
        description="Set the channel for drop notifications (defaults to current channel)",
    ):
        # Optional parameter; default=None makes it not required
        channel: str = opt.string("channel", "Channel mention like #general or numeric ID", default=None)

        @lightbulb.invoke
        async def invoke(self, ctx: lightbulb.Context) -> None:
            if not ctx.guild_id:
                await ctx.respond("This command must be used in a server.", ephemeral=True)
                return
            target_id: int | None = None
            raw = (self.channel or "").strip()
            if raw:
                m = re.match(r"^<#(\d+)>$", raw)
                if m:
                    target_id = int(m.group(1))
                elif raw.isdigit():
                    target_id = int(raw)
                else:
                    await ctx.respond("Provide a channel mention like #channel or a numeric ID.", ephemeral=True)
                    return
            else:
                target_id = int(ctx.channel_id)

            try:
                ch = await ctx.client.app.rest.fetch_channel(target_id)
                guild_id = getattr(ch, "guild_id", None)
                if guild_id and int(guild_id) != int(ctx.guild_id):
                    await ctx.respond("That channel is not in this server.", ephemeral=True)
                    return
            except Exception:
                await ctx.respond("Could not fetch that channel.", ephemeral=True)
                return

            shared.guild_store.set_channel_id(int(ctx.guild_id), int(target_id))
            await ctx.respond(f"Notifications channel set to <#{target_id}>.")

    return "drops_set_channel"
