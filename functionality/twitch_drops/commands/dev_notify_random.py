from __future__ import annotations

import random
import lightbulb

from ..differ import DropsDiff
from ..notifier import DropsNotifier
from .common import SharedContext, mark_deferred



def register(client: lightbulb.Client, shared: SharedContext) -> str:
	async def _reply(ctx: lightbulb.Context, deferred: bool, message: str) -> None:
		if deferred:
			try:
				await ctx.edit_initial_response(message)
				return
			except Exception:
				pass
		await ctx.respond(message, ephemeral=True)

	@client.register
	class DropsNotifyRandom(
		lightbulb.SlashCommand,
		name="drops_notify_random",
		description="Dev-only: trigger notifier for a random ACTIVE campaign",
	):
		@lightbulb.invoke
		async def invoke(self, ctx: lightbulb.Context) -> None:
			if not ctx.guild_id:
				await ctx.respond("This command must be used in a server.", ephemeral=True)
				return
			try:
				await ctx.defer(ephemeral=True)
			except Exception:
				deferred = False
			else:
				deferred = True
				mark_deferred(ctx)
			try:
				recs = await shared.get_campaigns_cached()
			except Exception:
				await _reply(ctx, deferred, "Failed to load campaigns.")
				return
			active = [r for r in recs if r.status == "ACTIVE"]
			if not active:
				await _reply(ctx, deferred, "No ACTIVE campaigns available to notify.")
				return
			r = random.choice(active)
			try:
				if ctx.guild_id:
					shared.guild_store.set_channel_id(int(ctx.guild_id), int(ctx.channel_id))
			except Exception:
				pass
			notifier = DropsNotifier(
				ctx.client.app,
				shared.guild_store,
				shared.favorites_store,
				shared.game_catalog,
			)
			game_key = None
			user_obj = getattr(ctx, "user", None) or getattr(ctx, "member", None) or getattr(ctx, "author", None)
			for candidate in (r.game_slug, r.game_name):
				if not candidate:
					continue
				entry = shared.game_catalog.get(candidate)
				if entry:
					game_key = entry.key
					break
				try:
					game_key = shared.game_catalog.normalize(candidate)
				except Exception:
					game_key = candidate.casefold()
				if game_key:
					break
			if game_key and user_obj is not None:
				try:
					shared.favorites_store.add_favorite(int(ctx.guild_id), int(getattr(user_obj, "id")), game_key)
				except Exception:
					pass
			try:
				await notifier.notify(DropsDiff(activated=[r]))
			except Exception as exc:
				await _reply(ctx, deferred, f"Failed to trigger notifier: {exc}")
				return
			await _reply(
				ctx,
				deferred,
				f"Triggered notifier for: {(r.game_name or r.name or r.id)}.",
			)

	return "drops_notify_random"
