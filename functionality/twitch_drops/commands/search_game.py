from __future__ import annotations

import lightbulb
from lightbulb import context as lb_context
from lightbulb.commands import options as opt
from hikari.files import Bytes

from ..embeds import build_campaign_embed
from ..images import build_benefits_collage
from .common import SharedContext, mark_deferred


def register(client: lightbulb.Client, shared: SharedContext) -> str:
    async def _autocomplete(ctx: lb_context.AutocompleteContext[str]) -> None:
        if not shared.game_catalog.is_ready():
            await ctx.respond([])
            return
        prefix = str(ctx.focused.value or "").strip()
        try:
            matches = shared.game_catalog.search(prefix, limit=25)
        except Exception:
            matches = []
        await ctx.respond([(entry.name, entry.key) for entry in matches])

    @client.register
    class DropsSearchGame(
        lightbulb.SlashCommand,
        name="drops_search_game",
        description="Browse active Twitch Drops campaigns by selecting a game",
    ):
        game: str = opt.string(
            "game",
            "Choose a game to view active drops",
            autocomplete=_autocomplete,
        )

        @lightbulb.invoke
        async def invoke(self, ctx: lightbulb.Context) -> None:
            key = (self.game or "").strip()
            entry = shared.game_catalog.get(key)
            if entry is None:
                await ctx.respond(
                    "Select a game from the provided suggestions to run this search.",
                    ephemeral=True,
                )
                return
            try:
                await ctx.defer()
            except Exception:
                pass
            else:
                mark_deferred(ctx)
            recs = await shared.get_campaigns_cached()
            matches = [
                r for r in recs if shared.game_catalog.matches_campaign(entry, r)
            ]
            if not matches:
                await ctx.respond(f"No active Twitch Drops campaigns found for **{entry.name}**.")
                return

            r = matches[0]
            e = build_campaign_embed(r, title_prefix="Selected Game")
            png, fname = await build_benefits_collage(
                r,
                limit=shared.ICON_LIMIT if shared.ICON_LIMIT >= 0 else 9,
                icon_size=(shared.ICON_SIZE, shared.ICON_SIZE),
                columns=shared.ICON_COLUMNS,
            )
            if png and fname:
                e.set_image(Bytes(png, fname))
            elif r.benefits and r.benefits[0].image_url:
                e.set_image(r.benefits[0].image_url)  # type: ignore[arg-type]
            if len(matches) > 1:
                e.set_footer("Multiple campaigns found; showing the first match.")
            await ctx.respond(embeds=[e])
            await shared.finalize_interaction(ctx)

    return "drops_search_game"
