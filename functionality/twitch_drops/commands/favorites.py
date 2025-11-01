from __future__ import annotations

from typing import List, Optional, Tuple, Sequence

import hikari
import lightbulb
from lightbulb import context as lb_context
from lightbulb.commands import options as opt

from ..game_catalog import GameEntry
from ..models import CampaignRecord
from ..embeds import build_campaign_embed
from .common import SharedContext, mark_deferred

CUSTOM_ID_PREFIX = "drops:fav"
REMOVE_SELECT_ID = f"{CUSTOM_ID_PREFIX}:remove"
REFRESH_BUTTON_ID = f"{CUSTOM_ID_PREFIX}:refresh"
CHECK_GOTO_ID = f"{CUSTOM_ID_PREFIX}:check"


class _LiteralComponent(hikari.api.special_endpoints.ComponentBuilder):
	"""Minimal ComponentBuilder implementation for static payloads."""

	__slots__ = ("_payload", "_type", "_id")

	def __init__(self, payload: dict[str, object], component_type: hikari.ComponentType) -> None:
		self._payload = payload
		self._type = component_type
		self._id = None

	@property
	def type(self) -> hikari.ComponentType:
		return self._type

	@property
	def id(self) -> int | None:
		return self._id

	def build(self) -> tuple[dict[str, object], Sequence[hikari.files.Resourceish]]:
		return self._payload, ()


def _build_overview(
	app: hikari.RESTAware,
	shared: SharedContext,
	guild_id: int,
	user_id: int,
) -> tuple[hikari.Embed, List[hikari.api.special_endpoints.ComponentBuilder]]:
	favorites = shared.favorites_store.get_user_favorites(guild_id, user_id)
	lines: list[str] = []
	select_entries: list[tuple[str, str]] = []
	for idx, key in enumerate(favorites, start=1):
		entry = shared.game_catalog.get(key)
		name = entry.name if entry else key
		lines.append(f"{idx}. **{name}**")
		select_entries.append((name, key))
	description = (
		"\n".join(lines)
		if lines
		else "You have no favorite games yet. Use `/drops_favorites add` to follow games you care about."
	)
	embed = hikari.Embed(title="Favorite Games", description=description[:4096])
	embed.set_footer("Use `/drops_favorites add` to add more games.")

	components: List[hikari.api.special_endpoints.ComponentBuilder] = []
	try:
		row = app.rest.build_message_action_row()
		row.add_button(hikari.ButtonStyle.SECONDARY, REFRESH_BUTTON_ID).set_label("Refresh")
		components.append(row)
	except Exception:
		components = []

	if select_entries and components is not None:
		try:
			select_row = app.rest.build_message_action_row()
			menu = select_row.add_text_select_menu(REMOVE_SELECT_ID)
			menu.set_placeholder("Remove favorites…")
			menu.set_min_values(1)
			menu.set_max_values(min(len(select_entries), 25))
			for name, key in select_entries[:25]:
				option = menu.add_option(name[:100], key[:100])
				option.set_description("Remove this game")
			components.append(select_row)
		except Exception:
			pass

	return embed, components


async def _find_active_campaigns(shared: SharedContext, entry: GameEntry | None) -> list[CampaignRecord]:
	if entry is None:
		return []
	try:
		recs = await shared.get_campaigns_cached()
	except Exception:
		return []
	matches: list[CampaignRecord] = []
	for rec in recs:
		if rec.status != "ACTIVE":
			continue
		try:
			if shared.game_catalog.matches_campaign(entry, rec):
				matches.append(rec)
		except Exception:
			continue
	return matches


async def _send_ephemeral_response(
	ctx: lightbulb.Context,
	deferred: bool,
	*,
	content: Optional[str] = None,
	embeds: Optional[List[hikari.Embed]] = None,
	components: Optional[Sequence[hikari.api.special_endpoints.ComponentBuilder]] = None,
) -> None:
	payload: dict[str, object] = {}
	if content is not None:
		payload["content"] = content
	if embeds is not None:
		payload["embeds"] = embeds
	if components is not None:
		payload["components"] = components
	if deferred:
		try:
			await ctx.edit_initial_response(**payload)
			return
		except hikari.errors.NotFoundError:
			pass
		except Exception:
			pass
	payload["flags"] = hikari.MessageFlag.EPHEMERAL
	await ctx.respond(**payload)


def _build_favorite_pages(
	shared: SharedContext,
	favorites: list[str],
	campaigns: list[CampaignRecord],
) -> list[tuple[GameEntry, list[CampaignRecord]]]:
	results: list[tuple[GameEntry, list[CampaignRecord]]] = []
	for key in favorites:
		entry = shared.game_catalog.get(key)
		if entry is None:
			continue
		matches: list[CampaignRecord] = []
		for campaign in campaigns:
			if campaign.status != "ACTIVE":
				continue
			try:
				if shared.game_catalog.matches_campaign(entry, campaign):
					matches.append(campaign)
			except Exception:
				continue
		matches.sort(key=lambda rec: rec.ends_ts or (10**10))
		results.append((entry, matches))
	return results


def _build_check_page_payload(
	app: hikari.RESTAware,
	user_id: int,
	pages: list[tuple[GameEntry, list[CampaignRecord]]],
	index: int,
) -> tuple[str, list[hikari.Embed], list[hikari.api.special_endpoints.ComponentBuilder]]:
	total = len(pages)
	index = max(0, min(index, total - 1))
	entry, campaigns = pages[index]
	content = f"Active Drops for **{entry.name}** ({index + 1}/{total})"
	embeds: list[hikari.Embed] = []
	for campaign in campaigns[:10]:
		embed = build_campaign_embed(campaign, title_prefix="Favorite Active")
		if campaign.benefits and campaign.benefits[0].image_url:
			embed.set_image(campaign.benefits[0].image_url)  # type: ignore[arg-type]
		embeds.append(embed)
	if not embeds:
		embed = hikari.Embed(title=entry.name, description="No active campaigns right now.")
		embeds.append(embed)
	else:
		remaining = len(campaigns) - len(embeds)
		if remaining > 0:
			embeds[-1].set_footer(f"+{remaining} more campaign(s) not shown in this view.")

	components: list[hikari.api.special_endpoints.ComponentBuilder] = []
	if total > 1:
		prev_target = max(index - 1, 0)
		next_target = min(index + 1, total - 1)
		row_payload: dict[str, object] = {
			"type": int(hikari.ComponentType.ACTION_ROW),
			"components": [
				{
					"type": int(hikari.ComponentType.BUTTON),
					"style": int(hikari.ButtonStyle.SECONDARY),
					"custom_id": f"{CHECK_GOTO_ID}:{user_id}:{prev_target}",
					"label": "Previous",
					"disabled": index == 0,
				},
				{
					"type": int(hikari.ComponentType.BUTTON),
					"style": int(hikari.ButtonStyle.SECONDARY),
					"custom_id": f"{CHECK_GOTO_ID}:{user_id}:{next_target}",
					"label": "Next",
					"disabled": index >= total - 1,
				},
			],
		}
		components.append(_LiteralComponent(row_payload, hikari.ComponentType.ACTION_ROW))

	return content, embeds, components


def register(client: lightbulb.Client, shared: SharedContext) -> str:
	async def _autocomplete_add_game(ctx: lb_context.AutocompleteContext[str]) -> None:
		if not shared.game_catalog.is_ready():
			await ctx.respond([])
			return
		prefix = str(ctx.focused.value or "").strip()
		try:
			matches = shared.game_catalog.search(prefix, limit=25)
		except Exception:
			matches = []
		await ctx.respond([(entry.name, entry.key) for entry in matches])

	async def _autocomplete_remove_game(ctx: lb_context.AutocompleteContext[str]) -> None:
		guild_id = getattr(ctx.interaction, "guild_id", None)
		user = getattr(ctx.interaction, "user", None)
		if guild_id is None or user is None:
			await ctx.respond([])
			return
		try:
			gid = int(guild_id)
			uid = int(user.id)
		except (TypeError, ValueError):
			await ctx.respond([])
			return
		candidates = shared.favorites_store.get_user_favorites(gid, uid)
		if not candidates:
			await ctx.respond([])
			return
		prefix = str(ctx.focused.value or "").strip().casefold()
		results: list[Tuple[str, str]] = []
		for key in candidates:
			entry = shared.game_catalog.get(key)
			name = entry.name if entry else key
			if prefix and prefix not in name.casefold():
				continue
			results.append((name, key))
		await ctx.respond(results[:25])

	group = lightbulb.Group(
		name="drops_favorites",
		description="Manage your favorite games for Drop alerts.",
	)

	@group.register
	class DropsFavoritesView(
		lightbulb.SlashCommand,
		name="view",
		description="Show the games you follow for Drop alerts.",
	):
		@lightbulb.invoke
		async def invoke(self, ctx: lightbulb.Context) -> None:
			if not ctx.guild_id:
				await ctx.respond("Favorites can only be managed inside a server.", ephemeral=True)
				return
			user_obj = getattr(ctx, "user", None) or getattr(ctx, "member", None) or getattr(ctx, "author", None)
			if user_obj is None:
				await ctx.respond("Could not resolve your user information.", ephemeral=True)
				return
			try:
				guild_id = int(ctx.guild_id)
				user_id = int(getattr(user_obj, "id"))
			except (TypeError, ValueError, AttributeError):
				await ctx.respond("Could not resolve your user information.", ephemeral=True)
				return

			app = ctx.client.app
			embed, components = _build_overview(app, shared, guild_id, user_id)
			await ctx.respond(embeds=[embed], components=components, ephemeral=True)

	@group.register
	class DropsFavoritesAdd(
		lightbulb.SlashCommand,
		name="add",
		description="Add a game to your favorites.",
	):
		game: str = opt.string(
			"game",
			"Pick the game you want to follow.",
			autocomplete=_autocomplete_add_game,
		)

		@lightbulb.invoke
		async def invoke(self, ctx: lightbulb.Context) -> None:
			if not ctx.guild_id:
				await ctx.respond("Favorites can only be managed inside a server.", ephemeral=True)
				return
			user_obj = getattr(ctx, "user", None) or getattr(ctx, "member", None) or getattr(ctx, "author", None)
			if user_obj is None:
				await ctx.respond("Could not resolve your user information.", ephemeral=True)
				return
			try:
				guild_id = int(ctx.guild_id)
				user_id = int(getattr(user_obj, "id"))
			except (TypeError, ValueError, AttributeError):
				await ctx.respond("Could not resolve your user information.", ephemeral=True)
				return

			app = ctx.client.app
			try:
				await ctx.defer(ephemeral=True)
			except Exception:
				deferred = False
			else:
				deferred = True
				mark_deferred(ctx)

			key = (self.game or "").strip()
			if not key:
				await _send_ephemeral_response(ctx, deferred, content="Select a game from the suggestions to add it.")
				return
			entry = shared.game_catalog.get(key)
			if entry is None:
				await _send_ephemeral_response(
					ctx,
					deferred,
					content="Select a game from the autocomplete suggestions to add it.",
				)
				return

			added = shared.favorites_store.add_favorite(guild_id, user_id, entry.key)
			if added:
				message = f"Added **{entry.name}** to your favorites."
			else:
				message = f"**{entry.name}** is already in your favorites."

			active = await _find_active_campaigns(shared, entry)
			embed, components = _build_overview(app, shared, guild_id, user_id)
			if active:
				lines = []
				for rec in active[:5]:
					ending = f" – ends <t:{rec.ends_ts}:R>" if rec.ends_ts else ""
					lines.append(f"- **{rec.name}**{ending}")
				embed.add_field(
					name="Active Campaigns Right Now",
					value="\n".join(lines)[:1024],
					inline=False,
				)

			await _send_ephemeral_response(
				ctx,
				deferred,
				content=message,
				embeds=[embed],
				components=components,
			)

	@group.register
	class DropsFavoritesCheck(
		lightbulb.SlashCommand,
		name="check",
		description="Check now active campaigns for your favorite games.",
	):
		@lightbulb.invoke
		async def invoke(self, ctx: lightbulb.Context) -> None:
			if not ctx.guild_id:
				await ctx.respond("Favorites can only be managed inside a server.", ephemeral=True)
				return
			user_obj = getattr(ctx, "user", None) or getattr(ctx, "member", None) or getattr(ctx, "author", None)
			if user_obj is None:
				await ctx.respond("Could not resolve your user information.", ephemeral=True)
				return
			try:
				guild_id = int(ctx.guild_id)
				user_id = int(getattr(user_obj, "id"))
			except Exception:
				await ctx.respond("Could not resolve your user information.", ephemeral=True)
				return
			try:
				await ctx.defer(ephemeral=True)
			except Exception:
				deferred = False
			else:
				deferred = True
				mark_deferred(ctx)

			favorites = shared.favorites_store.get_user_favorites(guild_id, user_id)
			if not favorites:
				await shared.finalize_interaction(ctx, message="You have no favorite games yet.")
				return

			try:
				recs = await shared.get_campaigns_cached()
			except Exception:
				await shared.finalize_interaction(ctx, message="Failed to load campaigns.")
				return

			pages = _build_favorite_pages(shared, favorites, recs)
			if not pages:
				await shared.finalize_interaction(ctx, message="No active campaigns for your favorites right now.")
				return

			content, embeds, components = _build_check_page_payload(ctx.client.app, user_id, pages, 0)
			await _send_ephemeral_response(
				ctx,
				deferred,
				content=content,
				embeds=embeds,
				components=components,
			)

	@group.register
	class DropsFavoritesRemove(
		lightbulb.SlashCommand,
		name="remove",
		description="Remove a game from your favorites.",
	):
		game: str = opt.string(
			"game",
			"Pick the favorite you want to unfollow.",
			autocomplete=_autocomplete_remove_game,
		)

		@lightbulb.invoke
		async def invoke(self, ctx: lightbulb.Context) -> None:
			if not ctx.guild_id:
				await ctx.respond("Favorites can only be managed inside a server.", ephemeral=True)
				return
			user_obj = getattr(ctx, "user", None) or getattr(ctx, "member", None) or getattr(ctx, "author", None)
			if user_obj is None:
				await ctx.respond("Could not resolve your user information.", ephemeral=True)
				return
			try:
				guild_id = int(ctx.guild_id)
				user_id = int(getattr(user_obj, "id"))
			except (TypeError, ValueError, AttributeError):
				await ctx.respond("Could not resolve your user information.", ephemeral=True)
				return

			app = ctx.client.app
			try:
				await ctx.defer(ephemeral=True)
			except Exception:
				deferred = False
			else:
				deferred = True
				mark_deferred(ctx)

			key = (self.game or "").strip()
			if not key:
				await _send_ephemeral_response(ctx, deferred, content="Select a favorite game to remove.")
				return

			removed = shared.favorites_store.remove_favorite(guild_id, user_id, key)
			if removed:
				message = "Removed that game from your favorites."
			else:
				message = "That game is not currently in your favorites."

			embed, components = _build_overview(app, shared, guild_id, user_id)
			await _send_ephemeral_response(
				ctx,
				deferred,
				content=message,
				embeds=[embed],
				components=components,
			)

	client.register(group)

	listen_target = getattr(client, "listen", None)
	if not callable(listen_target):
		app_attr = getattr(client, "app", None)
		listen_target = getattr(app_attr, "listen", None) if app_attr else None

	if callable(listen_target):
		@listen_target(hikari.InteractionCreateEvent)
		async def _favorites_component_handler(event: hikari.InteractionCreateEvent) -> None:
			interaction = event.interaction
			if not isinstance(interaction, hikari.ComponentInteraction):
				return
			custom_id = interaction.custom_id
			if custom_id is None:
				return
			if (
				custom_id not in {REMOVE_SELECT_ID, REFRESH_BUTTON_ID}
				and not custom_id.startswith(f"{CHECK_GOTO_ID}:")
			):
				return
			guild_id = getattr(interaction, "guild_id", None)
			user = getattr(interaction, "user", None)
			if guild_id is None or user is None:
				try:
					await interaction.create_initial_response(
						hikari.ResponseType.MESSAGE_UPDATE,
						content="Favorites can only be managed inside a server.",
					)
				except Exception:
					pass
				return
			try:
				gid = int(guild_id)
				uid = int(user.id)
			except (TypeError, ValueError):
				return

			app_local = interaction.app

			if custom_id == REMOVE_SELECT_ID:
				values = interaction.values or []
				removed = shared.favorites_store.remove_many(gid, uid, values)
				embed, components = _build_overview(app_local, shared, gid, uid)
				content = "Selected favorites removed." if removed else "Those games were not in your favorites."
				try:
					await interaction.create_initial_response(
						hikari.ResponseType.MESSAGE_UPDATE,
						content=content,
						embeds=[embed],
						components=components,
					)
				except Exception:
					pass
				return

			if custom_id == REFRESH_BUTTON_ID:
				embed, components = _build_overview(app_local, shared, gid, uid)
				try:
					await interaction.create_initial_response(
						hikari.ResponseType.MESSAGE_UPDATE,
						embeds=[embed],
						components=components,
					)
				except Exception:
					pass
				return

			if custom_id.startswith(f"{CHECK_GOTO_ID}:"):
				parts = custom_id.split(":")
				if len(parts) != 5:
					return
				try:
					target_uid = int(parts[3])
					target_index = int(parts[4])
				except (TypeError, ValueError):
					return
				if target_uid != uid:
					try:
						await interaction.create_initial_response(
							hikari.ResponseType.MESSAGE_CREATE,
							content="You cannot control another user's favorites pagination.",
							flags=hikari.MessageFlag.EPHEMERAL,
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
							content="Failed to refresh favorites.",
							embeds=[],
							components=[],
						)
					except Exception:
						pass
					return
				favorites = shared.favorites_store.get_user_favorites(gid, uid)
				pages = _build_favorite_pages(shared, favorites, recs)
				if not pages:
					try:
						await interaction.create_initial_response(
							hikari.ResponseType.MESSAGE_UPDATE,
							content="No active campaigns for your favorites right now.",
							embeds=[],
							components=[],
						)
					except Exception:
						pass
					return
				target_index = max(0, min(target_index, len(pages) - 1))
				content, embeds, components = _build_check_page_payload(app_local, uid, pages, target_index)
				try:
					await interaction.create_initial_response(
						hikari.ResponseType.MESSAGE_UPDATE,
						content=content,
						embeds=embeds,
						components=components,
					)
				except Exception:
					pass

	return "drops_favorites"
