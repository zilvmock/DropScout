from __future__ import annotations

"""Slash command registration for DropScout.

All command classes are defined and registered via the provided Lightbulb
client. This keeps the entrypoint tidy and the command logic modular.
"""

import os
import re
import asyncio
import difflib
from datetime import datetime, timezone, timedelta

import hikari
from hikari.files import Bytes, Resourceish
import lightbulb
from lightbulb.commands import options as opt

from .fetcher import DropsFetcher
from .embeds import build_campaign_embed
from .config import GuildConfigStore
# Optional collage of benefit icons
from .images import build_benefits_collage
from .models import CampaignRecord
from .notifier import DropsNotifier
from .differ import DropsDiff
# Note: export functionality removed per request


def register_commands(client: lightbulb.Client) -> list[str]:
	"""Register all DropScout commands on a Lightbulb client.

	Returns a list of command names that were registered. The return value is
	primarily useful for tests and does not need to be consumed in production.
	"""
	GUILD_STORE_PATH = os.getenv("TWITCH_GUILD_STORE_PATH", "data/guild_config.json")
	guild_store = GuildConfigStore(GUILD_STORE_PATH)

	# Collage config via env (set DROPS_ICON_LIMIT=0 to include all)
	ICON_LIMIT = int(os.getenv("DROPS_ICON_LIMIT", "9") or 9)
	ICON_SIZE = int(os.getenv("DROPS_ICON_SIZE", "96") or 96)
	ICON_COLUMNS = int(os.getenv("DROPS_ICON_COLUMNS", "3") or 3)
	# 0 or less means unlimited (attempt collages for all)
	MAX_ATTACH_PER_CMD = int(os.getenv("DROPS_MAX_ATTACHMENTS_PER_CMD", "0") or 0)
	SEND_DELAY_MS = int(os.getenv("DROPS_SEND_DELAY_MS", "350") or 350)
	FETCH_TTL = int(os.getenv("DROPS_FETCH_TTL_SECONDS", "120") or 120)

	_CACHE_DATA: list[CampaignRecord] = []
	_CACHE_EXP: float = 0.0

	async def _get_campaigns_cached() -> list[CampaignRecord]:
		nonlocal _CACHE_DATA, _CACHE_EXP
		now_ts = datetime.now(timezone.utc).timestamp()
		if _CACHE_DATA and now_ts < _CACHE_EXP:
			return _CACHE_DATA
		fetcher = DropsFetcher()
		data = await fetcher.fetch_condensed()
		_CACHE_DATA = data
		_CACHE_EXP = now_ts + FETCH_TTL
		return data

	@client.register
	class Hello(
		lightbulb.SlashCommand,
		name="hello",
		description="Say hello",
	):
		"""Trivial health-check command."""

		@lightbulb.invoke
		async def invoke(self, ctx: lightbulb.Context) -> None:
			await ctx.respond("Hello👋")

	@client.register
	class Help(
		lightbulb.SlashCommand,
		name="help",
		description="Show what this bot does and available commands",
	):
		"""Display a short description and the available commands."""

		@lightbulb.invoke
		async def invoke(self, ctx: lightbulb.Context) -> None:
			desc = (
				"DropScout surfaces ACTIVE Twitch Drops campaigns, builds image collages of rewards, "
				"and can notify your server when campaigns go live."
			)
			color = 0x235876
			e = hikari.Embed(title="DropScout Help", description=desc, color=color)
			e.add_field(
				name="/drops_active",
				value="List currently ACTIVE campaigns (with reward collages).",
				inline=False,
			)
			e.add_field(
				name="/drops_this_week",
				value="List ACTIVE campaigns ending before next Monday (UTC).",
				inline=False,
			)
			e.add_field(
				name="/drops_search_game <query>",
				value="Find the best-matching game with active Drops and show its campaign.",
				inline=False,
			)
			e.add_field(
				name="/drops_set_channel [channel]",
				value="Set the channel for notifications in this server.",
				inline=False,
			)
			e.add_field(
				name="/drops_channel",
				value="Show the configured notifications channel (or the default).",
				inline=False,
			)
			e.add_field(name="/hello", value="Quick health check.", inline=False)
			await ctx.respond(embeds=[e], ephemeral=True)



	@client.register
	class SetNotifyChannel(
		lightbulb.SlashCommand,
		name="drops_set_channel",
		description="Set the channel for drop notifications (defaults to current channel)",
	):
		"""Configure the notifications channel for the current guild."""

		channel: str = opt.string(
			"channel",
			"Channel mention or ID (optional)",
			default="",
		)

		@lightbulb.invoke
		async def invoke(self, ctx: lightbulb.Context) -> None:
			"""Set or validate the target channel, then persist it."""
			if not ctx.guild_id:
				await ctx.respond("This command must be used in a server.", ephemeral=True)
				return
			target_id: int | None = None
			raw = (self.channel or "").strip()
			if raw:
				m = re.match(r"^<#(\d+)>$", raw)
				if m:
					target_id = int(m.group(1))
				elif raw.isdigit():
					target_id = int(raw)
				else:
					await ctx.respond("Provide a channel mention like #channel or a numeric ID.", ephemeral=True)
					return
			else:
				target_id = int(ctx.channel_id)

			# Optional: validate the channel exists and belongs to the guild
			try:
				ch = await ctx.client.app.rest.fetch_channel(target_id)
				guild_id = getattr(ch, "guild_id", None)
				if guild_id and int(guild_id) != int(ctx.guild_id):
					await ctx.respond("That channel is not in this server.", ephemeral=True)
					return
			except Exception:
				await ctx.respond("Could not fetch that channel.", ephemeral=True)
				return

			guild_store.set_channel_id(int(ctx.guild_id), int(target_id))
			await ctx.respond(f"Notifications channel set to <#{target_id}>.")

	async def _send_embeds(
		ctx: lightbulb.Context,
		embeds: list[hikari.Embed],
		attachments_aligned: list[Resourceish | None] | None = None,
	) -> None:
		"""Send embeds, handling attachments reliably.

		If attachments_aligned is provided and contains any items, sends each
		embed as its own message with its corresponding attachment to ensure
		correct filename->embed mapping. Otherwise, sends in chunks of up to 10
		embeds per message for efficiency.
		"""
		if not embeds:
			await ctx.respond("No campaigns found.")
			return
		if attachments_aligned and any(a is not None for a in attachments_aligned):
			# Send each embed individually, setting the image resource directly to Bytes
			for e, a in zip(embeds, attachments_aligned):
				if a is not None and isinstance(a, Bytes):
					e.set_image(a)
				await ctx.client.app.rest.create_message(ctx.channel_id, embeds=[e])
				await asyncio.sleep(SEND_DELAY_MS / 1000)
			return
		# No attachments: chunk efficiently
		for i in range(0, len(embeds), 10):
			chunk = embeds[i : i + 10]
			await ctx.respond(embeds=chunk)

	@client.register
	class DropsActive(
		lightbulb.SlashCommand,
		name="drops_active",
		description="List campaigns that are currently ACTIVE",
	):
		"""Display currently active campaigns, ordered by end time."""

		@lightbulb.invoke
		async def invoke(self, ctx: lightbulb.Context) -> None:
			"""Fetch and list active campaigns as embeds."""
			# Defer quickly to keep the interaction alive during network calls
			try:
				await ctx.defer()
			except Exception:
				# If deferral fails (already acknowledged), continue
				pass
			recs = await _get_campaigns_cached()
			now = int(datetime.now(timezone.utc).timestamp())
			active = [r for r in recs if r.status == "ACTIVE"]
			active.sort(key=lambda r: (r.ends_ts or (now + 10**10)))
			embeds: list[hikari.Embed] = []
			attach_aligned: list[Resourceish | None] = []
			attaches_done = 0
			for r in active:
				e = build_campaign_embed(r, title_prefix="Active Campaign")
				png, fname = (None, None)
				if MAX_ATTACH_PER_CMD <= 0 or attaches_done < MAX_ATTACH_PER_CMD:
					png, fname = await build_benefits_collage(
						r,
						limit=ICON_LIMIT if ICON_LIMIT >= 0 else 9,
						icon_size=(ICON_SIZE, ICON_SIZE),
						columns=ICON_COLUMNS,
					)
				if png and fname:
					attach_aligned.append(Bytes(png, fname))
					attaches_done += 1
				else:
					# Fallback: show the first benefit icon directly if available
					if r.benefits and r.benefits[0].image_url:
						e.set_image(r.benefits[0].image_url)  # type: ignore[arg-type]
					attach_aligned.append(None)
				embeds.append(e)
			await _send_embeds(ctx, embeds, attach_aligned)

	@client.register
	class DropsChannel(
		lightbulb.SlashCommand,
		name="drops_channel",
		description="Show the current notifications channel for this server",
	):
		"""Report the configured notifications channel or fallback default."""

		@lightbulb.invoke
		async def invoke(self, ctx: lightbulb.Context) -> None:
			"""Display the effective notifications channel for this guild."""
			if not ctx.guild_id:
				await ctx.respond("This command must be used in a server.", ephemeral=True)
				return
			gid = int(ctx.guild_id)
			configured = guild_store.get_channel_id(gid)
			if configured:
				await ctx.respond(f"Notifications channel is <#{configured}>.")
				return
			# No explicit config; try to show system channel fallback
			try:
				g = await ctx.client.app.rest.fetch_guild(gid)
				scid = getattr(g, "system_channel_id", None)
				if scid:
					await ctx.respond(
						f"No channel configured. Defaults to system channel <#{int(scid)}>.")
				else:
					await ctx.respond(
						"No channel configured and no system channel set. Use /drops_set_channel.",
						ephemeral=True,
					)
			except Exception:
				await ctx.respond(
					"No channel configured. Use /drops_set_channel to set one.",
					ephemeral=True,
				)

	@client.register
	class DropsThisWeek(
		lightbulb.SlashCommand,
		name="drops_this_week",
		description="Show ACTIVE campaigns ending this week",
	):
		"""List only campaigns that are active now and end before next Monday (UTC)."""

		@lightbulb.invoke
		async def invoke(self, ctx: lightbulb.Context) -> None:
			try:
				await ctx.defer()
			except Exception:
				pass
			recs = await _get_campaigns_cached()
			now = datetime.now(timezone.utc)
			weekday = now.weekday()  # Monday=0
			days_ahead = (7 - weekday) or 7
			next_monday = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=days_ahead)
			horizon_ts = int(next_monday.timestamp())
			active_week = [r for r in recs if r.status == "ACTIVE" and (r.ends_ts or 0) <= horizon_ts]
			# Sort by ending soonest
			active_week.sort(key=lambda r: r.ends_ts or horizon_ts)
			embeds: list[hikari.Embed] = []
			attach_aligned: list[Resourceish | None] = []
			attaches_done = 0
			for r in active_week:
				e = build_campaign_embed(r, title_prefix="Active This Week")
				png, fname = (None, None)
				if MAX_ATTACH_PER_CMD <= 0 or attaches_done < MAX_ATTACH_PER_CMD:
					png, fname = await build_benefits_collage(
						r,
						limit=ICON_LIMIT if ICON_LIMIT >= 0 else 9,
						icon_size=(ICON_SIZE, ICON_SIZE),
						columns=ICON_COLUMNS,
					)
				if png and fname:
					attach_aligned.append(Bytes(png, fname))
					attaches_done += 1
				else:
					if r.benefits and r.benefits[0].image_url:
						e.set_image(r.benefits[0].image_url)  # type: ignore[arg-type]
					attach_aligned.append(None)
				embeds.append(e)
			await _send_embeds(ctx, embeds, attach_aligned)

	@client.register
	class DropsSearchGame(
		lightbulb.SlashCommand,
		name="drops_search_game",
		description="Search active campaigns by game name and show the best match",
	):
		"""Search the current ACTIVE campaigns by game name with fuzzy matching.

		Returns a single best-guess match. If the query is ambiguous, the
		response includes a hint to be more specific.
		"""

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
			recs = await _get_campaigns_cached()

			# Build index over game names only (one key per game name)
			def norm(s: str) -> str:
				s = s.casefold().strip()
				# collapse whitespace and drop punctuation differences
				s = re.sub(r"[\s_]+", " ", s)
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

			# Exact match on any key first
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
						# Ambiguity: more than one strong candidate and not an exact match
						strong = [m for m in matches if m[1] >= max(best_score - 5, 70)]
						ambiguous = len(strong) > 1 and nq != best_key
				except Exception:
					# Fallbacks
					close = difflib.get_close_matches(nq, keys, n=5, cutoff=0.4)
					if close:
						best_key = close[0]
						best_rec = key_to_items[best_key][0]
						ambiguous = len(close) > 1 and nq != best_key
					else:
						# Substring contains
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
				limit=ICON_LIMIT if ICON_LIMIT >= 0 else 9,
				icon_size=(ICON_SIZE, ICON_SIZE),
				columns=ICON_COLUMNS,
			)
			if png and fname:
				e.set_image(Bytes(png, fname))
			elif r.benefits and r.benefits[0].image_url:
				e.set_image(r.benefits[0].image_url)  # type: ignore[arg-type]
			hint = "Did you mean this game? If not, please be more specific." if ambiguous else None
			if hint:
				e.set_footer(hint)
			await ctx.respond(embeds=[e])

	# Dev-only: trigger notifier path with a random active campaign
	ENV = (os.getenv("ENV") or os.getenv("DROPSCOUT_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
	IS_PROD = (os.getenv("PRODUCTION") or os.getenv("IS_PRODUCTION") or "false").strip().lower() == "true" or ENV in ("prod", "production")
	if not IS_PROD:
		@client.register
		class DropsNotifyRandom(
			lightbulb.SlashCommand,
			name="drops_notify_random",
			description="Dev-only: trigger notifier for a random ACTIVE campaign",
		):
			"""Pick a random currently ACTIVE campaign and call the notifier.

			Sends messages to the same configured channels as the scheduled monitor
			would. This is intended for manual verification in non-production.
			"""

			@lightbulb.invoke
			async def invoke(self, ctx: lightbulb.Context) -> None:
				try:
					await ctx.defer(ephemeral=True)
				except Exception:
					pass
				recs = await _get_campaigns_cached()
				active = [r for r in recs if r.status == "ACTIVE"]
				if not active:
					await ctx.respond("No ACTIVE campaigns available to notify.", ephemeral=True)
					return
				import random
				r = random.choice(active)
				notifier = DropsNotifier(ctx.client.app, guild_store)
				try:
					await notifier.notify(DropsDiff(activated=[r]))
					await ctx.respond(
						f"Triggered notifier for: {(r.game_name or r.name or r.id)}.",
						ephemeral=True,
					)
				except Exception:
					await ctx.respond("Failed to trigger notifier.", ephemeral=True)


   # Return the set of commands we register (for testing convenience)
	names = [
		"hello",
		"help",
		"drops_active",
		"drops_this_week",
		"drops_set_channel",
		"drops_channel",
		"drops_search_game",
	]
	if not IS_PROD:
		names.append("drops_notify_random")
	return names
