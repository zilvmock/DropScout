from __future__ import annotations

from typing import List, Optional, Tuple

import asyncio

import hikari
import lightbulb
from lightbulb import context as lb_context
from lightbulb.commands import options as opt
from hikari.files import Bytes

from ..game_catalog import GameEntry
from ..models import CampaignRecord
from ..embeds import build_campaign_embed
from ..images import build_benefits_collage
from ..notifier import DropsNotifier
from .common import SharedContext

CUSTOM_ID_PREFIX = "drops:fav"
REMOVE_SELECT_ID = f"{CUSTOM_ID_PREFIX}:remove"
REFRESH_BUTTON_ID = f"{CUSTOM_ID_PREFIX}:refresh"


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
	components: Optional[List[hikari.api.special_endpoints.ComponentBuilder]] = None,
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
				setattr(ctx, "_dropscout_deferred", True)

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
				await ctx.defer()
				setattr(ctx, "_dropscout_deferred", True)
			except Exception:
				pass

			favorites = shared.favorites_store.get_user_favorites(guild_id, user_id)
			if not favorites:
				await shared.finalize_interaction(ctx, message="You have no favorite games yet.")
				return

			recs = await shared.get_campaigns_cached()
			entry_cache: dict[str, GameEntry | None] = {}
			matches: list[CampaignRecord] = []
			for rec in recs:
				if rec.status != "ACTIVE":
					continue
				for fav_key in favorites:
					entry = entry_cache.get(fav_key)
					if entry is None:
						entry = shared.game_catalog.get(fav_key)
						entry_cache[fav_key] = entry
					if entry and shared.game_catalog.matches_campaign(entry, rec):
						matches.append(rec)
						break
			if not matches:
				await shared.finalize_interaction(ctx, message="No active campaigns for your favorites right now.")
				return

			channel_id = shared.guild_store.get_channel_id(guild_id)
			if channel_id is None:
				try:
					channel_id = int(ctx.channel_id)
					shared.guild_store.set_channel_id(guild_id, channel_id)
				except Exception:
					channel_id = int(ctx.channel_id)

			notifier = DropsNotifier(
				ctx.client.app,
				shared.guild_store,
				shared.favorites_store,
				shared.game_catalog,
			)
			favorites_map = shared.favorites_store.get_guild_favorites(guild_id)

			attachments_budget = shared.MAX_ATTACH_PER_CMD if shared.MAX_ATTACH_PER_CMD > 0 else None
			attachments_used = 0
			sent = 0
			for campaign in matches:
				embed = build_campaign_embed(campaign, title_prefix="Now Active")
				png_bytes: bytes | None = None
				filename: str | None = None
				if attachments_budget is None or attachments_used < attachments_budget:
					png_bytes, filename = await build_benefits_collage(
						campaign,
						limit=shared.ICON_LIMIT if shared.ICON_LIMIT >= 0 else 9,
						icon_size=(shared.ICON_SIZE, shared.ICON_SIZE),
						columns=shared.ICON_COLUMNS,
					)
					if png_bytes and filename:
						attachments_used += 1
				if not png_bytes and campaign.benefits and campaign.benefits[0].image_url:
					embed.set_image(campaign.benefits[0].image_url)  # type: ignore[arg-type]
				attachment = None
				if png_bytes and filename:
					attachment = Bytes(png_bytes, filename)
					embed.set_image(attachment)

				keys = notifier._resolve_campaign_keys(campaign)
				watcher_ids = set(notifier._collect_watchers(favorites_map, keys))
				watcher_ids.add(user_id)
				mention_text = notifier._join_mentions(watcher_ids, limit=1800)

				try:
					await ctx.client.app.rest.create_message(
						channel_id,
						content=mention_text or f"<@{user_id}>",
						embeds=[embed],
					)
					sent += 1
				except Exception:
					continue
				await asyncio.sleep(shared.SEND_DELAY_MS / 1000)

			if sent == 0:
				await shared.finalize_interaction(ctx, message="Failed to send favorites alerts.")
				return

			await shared.finalize_interaction(ctx)

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
				setattr(ctx, "_dropscout_deferred", True)

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
			if interaction.custom_id not in {REMOVE_SELECT_ID, REFRESH_BUTTON_ID}:
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

			if interaction.custom_id == REMOVE_SELECT_ID:
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

			if interaction.custom_id == REFRESH_BUTTON_ID:
				embed, components = _build_overview(app_local, shared, gid, uid)
				try:
					await interaction.create_initial_response(
						hikari.ResponseType.MESSAGE_UPDATE,
						embeds=[embed],
						components=components,
					)
				except Exception:
					pass

	return "drops_favorites"
