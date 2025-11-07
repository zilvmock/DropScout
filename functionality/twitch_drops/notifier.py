from __future__ import annotations

"""Notification delivery for Twitch Drops changes.

Resolves target channels per guild and posts embeds describing changes.
Also attempts to include a collage of reward images in the embed image,
mirroring the behavior used by slash commands.
"""

import asyncio
import copy
import os
from dataclasses import dataclass
from typing import Iterable, List

import hikari
from hikari.files import Bytes

from .config import GuildConfigStore
from .differ import DropsDiff
from .embeds import build_campaign_embed
from .favorites import FavoritesStore
from .game_catalog import GameCatalog
from .images import build_benefits_collage
from .models import CampaignRecord


@dataclass(frozen=True, slots=True)
class NotifyTarget:
	guild_id: int
	channel_id: int


class DropsNotifier:
	"""Sends change notifications to each configured guild channel."""

	def __init__(
		self,
		app: hikari.RESTAware,
		guild_store: GuildConfigStore,
		favorites_store: FavoritesStore,
		game_catalog: GameCatalog,
	) -> None:
		"""Create a notifier bound to a Hikari app with REST access.

		Accepts any object implementing RESTAware (e.g., GatewayBot or RESTBot).
		"""
		self.app = app
		self.guild_store = guild_store
		self.favorites_store = favorites_store
		self.game_catalog = game_catalog
		# Collage + sending behavior (reuse command env vars when present)
		self.icon_limit = int(os.getenv("DROPS_ICON_LIMIT", "9") or 9)
		self.icon_size = int(os.getenv("DROPS_ICON_SIZE", "96") or 96)
		self.icon_cols = int(os.getenv("DROPS_ICON_COLUMNS", "3") or 3)
		# Prefer a notify-specific cap if provided; otherwise share command cap
		max_att = os.getenv("DROPS_MAX_ATTACHMENTS_PER_NOTIFY", os.getenv("DROPS_MAX_ATTACHMENTS_PER_CMD", "0"))
		self.max_attachments = int(max_att or 0)
		self.send_delay_ms = int(os.getenv("DROPS_SEND_DELAY_MS", "350") or 350)

	async def _resolve_targets(self) -> list[NotifyTarget]:
		"""Return the list of channels (with guild context) to notify."""
		targets: list[NotifyTarget] = []
		try:
			guilds = await self.app.rest.fetch_my_guilds()
		except Exception:
			guilds = []
		for g in guilds:
			gid = int(g.id)
			cid = self.guild_store.get_channel_id(gid)
			if cid:
				targets.append(NotifyTarget(guild_id=gid, channel_id=int(cid)))
				continue
			scid = getattr(g, "system_channel_id", None)
			if scid:
				targets.append(NotifyTarget(guild_id=gid, channel_id=int(scid)))
		return targets

	def _resolve_campaign_keys(self, campaign: "CampaignRecord") -> set[str]:
		keys: set[str] = set()
		for candidate in (campaign.game_slug, campaign.game_name):
			if not candidate:
				continue
			entry = self.game_catalog.get(candidate)
			if entry is not None:
				keys.add(entry.key)
			else:
				try:
					normalized = self.game_catalog.normalize(candidate)
				except Exception:
					normalized = candidate.casefold()
				if normalized:
					keys.add(normalized)
		return keys

	def _collect_watchers(self, favorites_map: dict[int, set[str]], keys: Iterable[str]) -> list[int]:
		target = {k for k in keys if k}
		if not target:
			return []
		users = [uid for uid, games in favorites_map.items() if games & target]
		users.sort()
		return users

	def _join_mentions(self, user_ids: Iterable[int], *, limit: int) -> tuple[str, list[int]]:
		mentions: list[str] = []
		included_ids: list[int] = []
		total = 0
		for uid in sorted(dict.fromkeys(int(u) for u in user_ids)):
			token = f"<@{uid}>"
			added = len(token) if not mentions else len(token) + 1
			if total + added > limit:
				if mentions:
					mentions.append("…")
				break
			mentions.append(token)
			included_ids.append(uid)
			total += added
		return " ".join(mentions), included_ids

	async def notify(self, diff: DropsDiff) -> None:
		"""Post embeds for any newly ACTIVE campaigns (with reward collages)."""
		if not diff.activated:
			return

		payloads: List[tuple["CampaignRecord", hikari.Embed, bytes | None, str | None]] = []
		attachments_budget = self.max_attachments if self.max_attachments > 0 else None
		attachments_used = 0

		for campaign in diff.activated:
			embed = build_campaign_embed(campaign, title_prefix="Now Active")
			png_bytes: bytes | None = None
			filename: str | None = None
			if attachments_budget is None or attachments_used < attachments_budget:
				png_bytes, filename = await build_benefits_collage(
					campaign,
					limit=self.icon_limit if self.icon_limit >= 0 else 9,
					icon_size=(self.icon_size, self.icon_size),
					columns=self.icon_cols,
				)
				if png_bytes and filename:
					attachments_used += 1
			if not png_bytes and campaign.benefits and campaign.benefits[0].image_url:
				embed.set_image(campaign.benefits[0].image_url)  # type: ignore[arg-type]
			payloads.append((campaign, embed, png_bytes, filename))

		if not payloads:
			return

		targets = await self._resolve_targets()
		if not targets:
			return

		for target in targets:
			favorites_map = self.favorites_store.get_guild_favorites(target.guild_id)
			for campaign, base_embed, png_bytes, filename in payloads:
				embed = copy.deepcopy(base_embed)
				keys = self._resolve_campaign_keys(campaign)
				watchers = self._collect_watchers(favorites_map, keys)
				content = None
				user_mentions = hikari.UNDEFINED
				if watchers:
					mention_text, included = self._join_mentions(watchers, limit=1800)
					if mention_text:
						content = f"Favorites alert: {mention_text}"
						user_mentions = included or hikari.UNDEFINED
				try:
					if png_bytes and filename:
						attachment = Bytes(png_bytes, filename)
						embed.set_image(attachment)
						await self.app.rest.create_message(
							target.channel_id,
							content=content,
							embeds=[embed],
							user_mentions=user_mentions,
						)
					else:
						await self.app.rest.create_message(
							target.channel_id,
							content=content,
							embeds=[embed],
							user_mentions=user_mentions,
						)
				except Exception:
					pass
				await asyncio.sleep(self.send_delay_ms / 1000)
