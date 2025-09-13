from __future__ import annotations

import difflib

import hikari
from hikari.files import Bytes
import lightbulb
from lightbulb.commands import options as opt

from ..embeds import build_campaign_embed
from ..images import build_benefits_collage
from ..models import CampaignRecord
from .common import SharedContext


def register(client: lightbulb.Client, shared: SharedContext) -> str:
    @client.register
    class DropsSearchGame(
        lightbulb.SlashCommand,
        name="drops_search_game",
        description="Search active campaigns by game name and show the best match",
    ):
        query: str = opt.string("query", "Game name to search for")

        @lightbulb.invoke
        async def invoke(self, ctx: lightbulb.Context) -> None:
            q = (self.query or "").strip()
            if not q:
                await ctx.respond("Provide a game name, e.g. 'Call of Duty'.", ephemeral=True)
                return
            try:
                await ctx.defer()
            except Exception:
                pass
            recs = await shared.get_campaigns_cached()

            def norm(s: str) -> str:
                import re as _re

                s = s.casefold().strip()
                s = _re.sub(r"[\s_]+", " ", s)
                return s

            key_to_items: dict[str, list[CampaignRecord]] = {}
            keys: list[str] = []
            for r in recs:
                game = (r.game_name or "").strip()
                if not game:
                    continue
                gk = norm(game)
                key_to_items.setdefault(gk, []).append(r)
                if gk not in keys:
                    keys.append(gk)

            nq = norm(q)
            best_rec: CampaignRecord | None = None
            ambiguous = False

            if nq in key_to_items:
                best_rec = key_to_items[nq][0]
            else:
                try:
                    from rapidfuzz import process, fuzz  # type: ignore

                    matches = process.extract(nq, keys, scorer=fuzz.token_set_ratio, limit=5)
                    if matches:
                        best_key, best_score, _ = matches[0]
                        if best_score >= 45:
                            best_rec = key_to_items[best_key][0]
                        strong = [m for m in matches if m[1] >= max(best_score - 5, 70)]
                        ambiguous = len(strong) > 1 and nq != best_key
                except Exception:
                    close = difflib.get_close_matches(nq, keys, n=5, cutoff=0.4)
                    if close:
                        best_key = close[0]
                        best_rec = key_to_items[best_key][0]
                        ambiguous = len(close) > 1 and nq != best_key
                    else:
                        contains = [k for k in keys if nq in k]
                        if contains:
                            best_key = contains[0]
                            best_rec = key_to_items[best_key][0]
                            ambiguous = len(contains) > 1 and nq != best_key

            if not best_rec:
                await ctx.respond("No matching games with drops found.")
                return
            r = best_rec
            e = build_campaign_embed(r, title_prefix="Best Match")
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
            hint = "Did you mean this game? If not, please be more specific." if ambiguous else None
            if hint:
                e.set_footer(hint)
            await ctx.respond(embeds=[e])
            await shared.finalize_interaction(ctx)

    return "drops_search_game"
