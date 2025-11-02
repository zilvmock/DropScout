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
            embed = hikari.Embed(title="DropScout Help", description=desc, color=color)

            if not ctx.guild_id:
                channel_status = (
                    "Run this command inside a server to see or change the notifications channel.\n"
                    "Use `/drops_set_channel [channel]` to choose where alerts post."
                )
            else:
                gid = int(ctx.guild_id)
                configured = shared.guild_store.get_channel_id(gid)
                if configured:
                    channel_status = (
                        f"Currently sending notifications to <#{configured}>.\n"
                        "Use `/drops_set_channel [channel]` to switch the active channel."
                    )
                else:
                    try:
                        guild = await ctx.client.app.rest.fetch_guild(gid)
                    except Exception:
                        guild = None
                    raw_system_id = getattr(guild, "system_channel_id", None) if guild else None
                    system_channel_id = int(raw_system_id) if raw_system_id else None
                    if system_channel_id:
                        channel_status = (
                            f"No custom channel configured. Defaulting to the system channel <#{system_channel_id}>.\n"
                            "Use `/drops_set_channel [channel]` to pick a different destination."
                        )
                    else:
                        channel_status = (
                            "No channel configured yet. Use `/drops_set_channel [channel]` to pick where notifications go."
                        )

            embed.add_field(
                name="Notifications Channel",
                value=channel_status,
                inline=False,
            )

            embed.add_field(
                name="Browse Drops",
                value=(
                    "- `/drops_search_game <game>` — Pick a game and preview its active campaign."
                ),
                inline=False,
            )

            embed.add_field(
                name="Favorites Toolkit",
                value=(
                    "- `/drops_favorites view` — See the games you follow.\n"
                    "- `/drops_favorites add <game>` — Follow a game for quick access.\n"
                    "- `/drops_favorites check` — Check active campaigns for your favorites now.\n"
                    "- `/drops_favorites remove <game>` — Unfollow a game."
                ),
                inline=False,
            )

            embed.add_field(
                name="Utilities",
                value=(
                    "- `/drops_channel` — Show the configured notifications channel.\n"
                    "- `/drops_set_channel [channel]` — Change where notifications are posted.\n"
                    "- `/hello` — Quick health check.\n"
                    "- `/help` — Show this guide."
                ),
                inline=False,
            )

            await ctx.respond(embeds=[embed], ephemeral=True)

    return "help"
