from __future__ import annotations

"""Notification delivery for Twitch Drops changes.

Resolves target channels per guild and posts embeds describing changes.
"""

import hikari

from .differ import DropsDiff
from .embeds import build_campaign_embed
from .config import GuildConfigStore


class DropsNotifier:
	"""Sends change notifications to each configured guild channel."""

	def __init__(self, app: hikari.GatewayBot, guild_store: GuildConfigStore) -> None:
		"""Create a notifier bound to the Hikari app and a config store."""
		self.app = app
		self.guild_store = guild_store

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
		"""Post embeds for any newly ACTIVE campaigns."""
		embeds: list[hikari.Embed] = []
		for c in diff.activated:
			embeds.append(build_campaign_embed(c, title_prefix="Now Active"))

		if not embeds:
			return

		targets = await self._resolve_targets()
		if not targets:
			return

		for target in targets:
			for i in range(0, len(embeds), 10):
				chunk = embeds[i : i + 10]
				try:
					await self.app.rest.create_message(target, embeds=chunk)
				except Exception:
					pass
