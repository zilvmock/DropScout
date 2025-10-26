from __future__ import annotations

"""Background monitoring loop for Twitch Drops changes."""

import asyncio
from typing import Optional

import hikari

from .state import DropsStateStore
from .fetcher import DropsFetcher
from .differ import DropsDiffer
from .notifier import DropsNotifier
from .config import GuildConfigStore
from .game_catalog import get_game_catalog


class DropsMonitor:
	"""Periodically polls Twitch data and notifies on changes."""

	def __init__(
		self,
		app: hikari.GatewayBot,
		*,
		interval_minutes: int = 30,
		state_path: str = "data/campaigns_state.json",
		guild_store_path: str = "data/guild_config.json",
		notify_on_boot: bool = False,
	) -> None:
		"""Configure the monitor for a Hikari app.

		interval_minutes controls the polling cadence; state_path stores the
		previous snapshot; guild_store_path holds per-guild notification channels.
		"""
		self.app = app
		self.interval_minutes = max(1, int(interval_minutes))
		self.store = DropsStateStore(state_path)
		self.fetcher = DropsFetcher()
		self.notifier = DropsNotifier(app, GuildConfigStore(guild_store_path))
		self.notify_on_boot = notify_on_boot
		self._task: Optional[asyncio.Task] = None

	def start(self) -> None:
		"""Start the monitoring task if not already running."""
		if self._task is None or self._task.done():
			self._task = asyncio.create_task(self._run_loop(), name="drops-monitor")

	async def stop(self) -> None:
		"""Cancel and await the monitoring task if running."""
		if self._task and not self._task.done():
			self._task.cancel()
			try:
				await self._task
			except asyncio.CancelledError:
				pass

	async def _run_loop(self) -> None:
		"""Main loop: fetch → diff → notify → persist → sleep."""
		prev = self.store.load()
		try:
			get_game_catalog().merge_state_snapshot(prev)
		except Exception:
			pass
		first_run = True
		differ = DropsDiffer()
		while True:
			try:
				curr = await self.fetcher.fetch_condensed()
				diff = differ.diff(prev, curr)
				if first_run and not self.notify_on_boot:
					pass
				else:
					await self.notifier.notify(diff)
				self.store.save(curr)
				prev = self.store.load()
				first_run = False
			except Exception:
				# Intentionally swallow to keep the loop healthy
				pass
			await asyncio.sleep(self.interval_minutes * 60)
