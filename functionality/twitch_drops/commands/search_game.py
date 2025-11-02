from __future__ import annotations

import secrets
from collections import OrderedDict
from typing import Sequence

import hikari
import lightbulb
from lightbulb import context as lb_context
from lightbulb.commands import options as opt
from hikari.files import Bytes

from ..embeds import build_campaign_embed
from ..game_catalog import GameEntry
from ..models import CampaignRecord
from ..images import build_benefits_collage
from .common import SharedContext, mark_deferred

CUSTOM_ID_PREFIX = "drops:search"
GOTO_CUSTOM_ID = f"{CUSTOM_ID_PREFIX}:goto"
_SESSION_LIMIT = 256
_search_sessions: OrderedDict[str, str] = OrderedDict()


class _LiteralComponent(hikari.api.special_endpoints.ComponentBuilder):
    """Minimal ComponentBuilder for static button payloads."""

    __slots__ = ("_payload", "_type", "_id")

    def __init__(self, payload: dict[str, object], component_type: hikari.ComponentType) -> None:
        self._payload = payload
        self._type = component_type
        self._id: int | None = None

    @property
    def type(self) -> hikari.ComponentType:
        return self._type

    @property
    def id(self) -> int | None:
        return self._id

    def build(self) -> tuple[dict[str, object], Sequence[hikari.files.Resourceish]]:
        return self._payload, ()


def _store_session(token: str, game_key: str) -> None:
    _search_sessions[token] = game_key
    _search_sessions.move_to_end(token, last=True)
    while len(_search_sessions) > _SESSION_LIMIT:
        _search_sessions.popitem(last=False)


def _resolve_session(token: str) -> str | None:
    value = _search_sessions.get(token)
    if value is None:
        return None
    _search_sessions.move_to_end(token, last=True)
    return value


def _resolve_user_id(ctx: lightbulb.Context) -> int | None:
    user_obj = getattr(ctx, "author", None) or getattr(ctx, "user", None) or getattr(ctx, "member", None)
    if user_obj is None:
        return None
    try:
        return int(getattr(user_obj, "id"))
    except (TypeError, ValueError, AttributeError):
        return None


async def _build_page_payload(
    shared: SharedContext,
    entry: GameEntry,
    campaigns: list[CampaignRecord],
    index: int,
    *,
    token: str | None,
    user_id: int | None,
) -> tuple[str, list[hikari.Embed], list[hikari.api.special_endpoints.ComponentBuilder]]:
    total = len(campaigns)
    index = max(0, min(index, total - 1))
    campaign = campaigns[index]
    embed = build_campaign_embed(campaign, title_prefix="Selected Game")
    png = fname = None
    try:
        png, fname = await build_benefits_collage(
            campaign,
            limit=shared.ICON_LIMIT if shared.ICON_LIMIT >= 0 else 9,
            icon_size=(shared.ICON_SIZE, shared.ICON_SIZE),
            columns=shared.ICON_COLUMNS,
        )
    except Exception:
        png = fname = None
    if png and fname:
        embed.set_image(Bytes(png, fname))
    elif campaign.benefits and campaign.benefits[0].image_url:
        embed.set_image(campaign.benefits[0].image_url)  # type: ignore[arg-type]
    embed.set_footer(f"Campaign {index + 1}/{total}")
    content = f"Active Drops for **{entry.name}** ({index + 1}/{total})"

    components: list[hikari.api.special_endpoints.ComponentBuilder] = []
    if total > 1 and token is not None and user_id is not None:
        prev_target = max(index - 1, 0)
        next_target = min(index + 1, total - 1)
        row_payload: dict[str, object] = {
            "type": int(hikari.ComponentType.ACTION_ROW),
            "components": [
                {
                    "type": int(hikari.ComponentType.BUTTON),
                    "style": int(hikari.ButtonStyle.SECONDARY),
                    "custom_id": f"{GOTO_CUSTOM_ID}:{token}:{user_id}:{prev_target}",
                    "label": "Previous",
                    "disabled": index == 0,
                },
                {
                    "type": int(hikari.ComponentType.BUTTON),
                    "style": int(hikari.ButtonStyle.SECONDARY),
                    "custom_id": f"{GOTO_CUSTOM_ID}:{token}:{user_id}:{next_target}",
                    "label": "Next",
                    "disabled": index >= total - 1,
                },
            ],
        }
        components.append(_LiteralComponent(row_payload, hikari.ComponentType.ACTION_ROW))

    return content, [embed], components


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
            user_id = _resolve_user_id(ctx)
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
            matches.sort(key=lambda rec: rec.ends_ts or (10**10))
            if not matches:
                await ctx.respond(f"No active Twitch Drops campaigns found for **{entry.name}**.")
                return

            token: str | None = None
            if len(matches) > 1 and user_id is not None:
                token = secrets.token_urlsafe(8)
                _store_session(token, entry.key)

            content, embeds, components = await _build_page_payload(
                shared,
                entry,
                matches,
                0,
                token=token,
                user_id=user_id,
            )
            await ctx.respond(content=content, embeds=embeds, components=components or None)
            await shared.finalize_interaction(ctx)

    listen_target = getattr(client, "listen", None)
    if not callable(listen_target):
        app_attr = getattr(client, "app", None)
        listen_target = getattr(app_attr, "listen", None) if app_attr else None

    if callable(listen_target):

        @listen_target(hikari.InteractionCreateEvent)
        async def _search_pagination_handler(event: hikari.InteractionCreateEvent) -> None:
            interaction = event.interaction
            if not isinstance(interaction, hikari.ComponentInteraction):
                return
            custom_id = interaction.custom_id
            if custom_id is None or not custom_id.startswith(f"{GOTO_CUSTOM_ID}:"):
                return
            parts = custom_id.split(":")
            if len(parts) != 6:
                return
            token = parts[3]
            try:
                target_uid = int(parts[4])
                target_index = int(parts[5])
            except (TypeError, ValueError):
                return
            user_obj = getattr(interaction, "user", None)
            if user_obj is None:
                return
            try:
                uid = int(user_obj.id)
            except (TypeError, ValueError):
                return
            if uid != target_uid:
                try:
                    await interaction.create_initial_response(
                        hikari.ResponseType.MESSAGE_CREATE,
                        content="You cannot control another user's search results.",
                        flags=hikari.MessageFlag.EPHEMERAL,
                    )
                except Exception:
                    pass
                return
            game_key = _resolve_session(token)
            if not game_key:
                try:
                    await interaction.create_initial_response(
                        hikari.ResponseType.MESSAGE_UPDATE,
                        content="Search session expired. Run `/drops_search_game` again.",
                        embeds=[],
                        components=[],
                    )
                except Exception:
                    pass
                return
            entry = shared.game_catalog.get(game_key)
            if entry is None:
                try:
                    await interaction.create_initial_response(
                        hikari.ResponseType.MESSAGE_UPDATE,
                        content="Could not find that game anymore.",
                        embeds=[],
                        components=[],
                    )
                except Exception:
                    pass
                return
            try:
                recs = await shared.get_campaigns_cached()
            except Exception:
                try:
                    await interaction.create_initial_response(
                        hikari.ResponseType.MESSAGE_UPDATE,
                        content="Failed to refresh campaigns.",
                        embeds=[],
                        components=[],
                    )
                except Exception:
                    pass
                return
            matches = [
                r for r in recs if shared.game_catalog.matches_campaign(entry, r)
            ]
            matches.sort(key=lambda rec: rec.ends_ts or (10**10))
            if not matches:
                try:
                    await interaction.create_initial_response(
                        hikari.ResponseType.MESSAGE_UPDATE,
                        content=f"No active Twitch Drops campaigns found for **{entry.name}**.",
                        embeds=[],
                        components=[],
                    )
                except Exception:
                    pass
                return
            target_index = max(0, min(target_index, len(matches) - 1))
            content, embeds, components = await _build_page_payload(
                shared,
                entry,
                matches,
                target_index,
                token=token,
                user_id=uid,
            )
            try:
                await interaction.create_initial_response(
                    hikari.ResponseType.MESSAGE_UPDATE,
                    content=content,
                    embeds=embeds,
                    components=components or None,
                )
            except Exception:
                pass

    return "drops_search_game"
