from __future__ import annotations

"""Notification delivery for Twitch Drops changes.

Resolves target channels per guild and posts embeds describing changes.
Also attempts to include a collage of reward images in the embed image,
mirroring the behavior used by slash commands.
"""

import asyncio
import os

import hikari
from hikari.files import Bytes

from .differ import DropsDiff
from .embeds import build_campaign_embed
from .config import GuildConfigStore
from .images import build_benefits_collage


class DropsNotifier:
	"""Sends change notifications to each configured guild channel."""

	def __init__(self, app: hikari.RESTAware, guild_store: GuildConfigStore) -> None:
		"""Create a notifier bound to a Hikari app with REST access.

		Accepts any object implementing RESTAware (e.g., GatewayBot or RESTBot).
		"""
		self.app = app
		self.guild_store = guild_store
		# Collage + sending behavior (reuse command env vars when present)
		self.icon_limit = int(os.getenv("DROPS_ICON_LIMIT", "9") or 9)
		self.icon_size = int(os.getenv("DROPS_ICON_SIZE", "96") or 96)
		self.icon_cols = int(os.getenv("DROPS_ICON_COLUMNS", "3") or 3)
		# Prefer a notify-specific cap if provided; otherwise share command cap
		max_att = os.getenv("DROPS_MAX_ATTACHMENTS_PER_NOTIFY", os.getenv("DROPS_MAX_ATTACHMENTS_PER_CMD", "0"))
		self.max_attachments = int(max_att or 0)
		self.send_delay_ms = int(os.getenv("DROPS_SEND_DELAY_MS", "350") or 350)

	async def _resolve_targets(self) -> list[int]:
		"""Return the list of channel IDs to notify across all guilds."""
		channel_ids: list[int] = []
		try:
			guilds = await self.app.rest.fetch_my_guilds()
		except Exception:
			guilds = []
		for g in guilds:
			cid = self.guild_store.get_channel_id(int(g.id))
			if cid:
				channel_ids.append(cid)
				continue
			scid = getattr(g, "system_channel_id", None)
			if scid:
				channel_ids.append(int(scid))
		return channel_ids

	async def notify(self, diff: DropsDiff) -> None:
		"""Post embeds for any newly ACTIVE campaigns (with reward collages)."""
		embeds: list[hikari.Embed] = []
		attachments_aligned: list[Bytes | None] = []
		attaches_done = 0

		for c in diff.activated:
			e = build_campaign_embed(c, title_prefix="Now Active")

			# Attempt to build a collage; cap total attachments if configured
			png, fname = (None, None)
			if self.max_attachments <= 0 or attaches_done < self.max_attachments:
				png, fname = await build_benefits_collage(
					c,
					limit=self.icon_limit if self.icon_limit >= 0 else 9,
					icon_size=(self.icon_size, self.icon_size),
					columns=self.icon_cols,
				)

			if png and fname:
				attachments_aligned.append(Bytes(png, fname))
				attaches_done += 1
			else:
				# Fallback: show the first benefit icon directly if available
				if c.benefits and c.benefits[0].image_url:
					e.set_image(c.benefits[0].image_url)  # type: ignore[arg-type]
				attachments_aligned.append(None)

			embeds.append(e)

		if not embeds:
			return

		targets = await self._resolve_targets()
		if not targets:
			return

		any_attachments = any(a is not None for a in attachments_aligned)
		for target in targets:
			if any_attachments:
				# Send each embed individually to ensure correct attachment mapping
				for e, a in zip(embeds, attachments_aligned):
					try:
						if a is not None:
							e.set_image(a)
						await self.app.rest.create_message(target, embeds=[e])
					except Exception:
						pass
					await asyncio.sleep(self.send_delay_ms / 1000)
			else:
				# No attachments: send in chunks for efficiency
				for i in range(0, len(embeds), 10):
					chunk = embeds[i : i + 10]
					try:
						await self.app.rest.create_message(target, embeds=chunk)
					except Exception:
						pass
