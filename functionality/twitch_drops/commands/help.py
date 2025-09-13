from __future__ import annotations

import hikari
import lightbulb

from .common import SharedContext


def register(client: lightbulb.Client, shared: SharedContext) -> str:
    @client.register
    class Help(
        lightbulb.SlashCommand,
        name="help",
        description="Show what this bot does and available commands",
    ):
        @lightbulb.invoke
        async def invoke(self, ctx: lightbulb.Context) -> None:
            desc = (
                "DropScout surfaces ACTIVE Twitch Drops campaigns, builds image collages of rewards, "
                "and can notify your server when campaigns go live."
            )
            color = 0x235876
            e = hikari.Embed(title="DropScout Help", description=desc, color=color)
            e.add_field(
                name="/drops_active",
                value="List currently ACTIVE campaigns (with reward collages).",
                inline=False,
            )
            e.add_field(
                name="/drops_this_week",
                value="List ACTIVE campaigns ending before next Monday (UTC).",
                inline=False,
            )
            e.add_field(
                name="/drops_search_game <query>",
                value="Find the best-matching game with active Drops and show its campaign.",
                inline=False,
            )
            e.add_field(
                name="/drops_set_channel [channel]",
                value="Set the channel for notifications in this server.",
                inline=False,
            )
            e.add_field(
                name="/drops_channel",
                value="Show the configured notifications channel (or the default).",
                inline=False,
            )
            e.add_field(name="/hello", value="Quick health check.", inline=False)
            await ctx.respond(embeds=[e], ephemeral=True)

    return "help"

