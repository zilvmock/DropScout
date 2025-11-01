from __future__ import annotations

"""Caching and lookup helpers for Twitch game metadata.

The catalog merges popular games from the Helix `games/top` endpoint with any
games observed across active and historical Drop campaigns. A ready flag keeps
commands paused until the cache has been regenerated for the current bot run.
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Iterable, Optional

import aiohttp
import lightbulb
from lightbulb import exceptions as lb_exceptions
from lightbulb.commands import execution as lb_execution

from .models import CampaignRecord
from .twitch_drops import ANDROID_CLIENT_ID, ensure_env_access_token

__all__ = [
	"GameCatalog",
	"GameEntry",
	"GameCatalogUnavailableError",
	"GameCatalogNotReady",
	"ensure_game_catalog_ready_hook",
	"register_game_catalog_handlers",
	"get_game_catalog",
	"warm_game_catalog",
]


def _norm(value: str) -> str:
	"""Normalize game identifiers for consistent matching."""
	value = value.casefold().strip()
	value = re.sub(r"[\s_]+", " ", value)
	return value


class GameCatalogUnavailableError(RuntimeError):
	"""Raised when Twitch game metadata cannot be fetched."""


class GameCatalogNotReady(lb_exceptions.ExecutionException):
	"""Raised when commands are invoked before the game catalog is ready."""


@dataclass
class GameEntry:
	"""Represents a known Twitch game in the autocomplete catalog."""

	key: str
	name: str
	slug: Optional[str] = None
	twitch_id: Optional[str] = None
	box_art_url: Optional[str] = None
	weight: int = 0
	aliases: list[str] = field(default_factory=list)
	sources: list[str] = field(default_factory=list)

	def copy(self) -> "GameEntry":
		return GameEntry(
			key=self.key,
			name=self.name,
			slug=self.slug,
			twitch_id=self.twitch_id,
			box_art_url=self.box_art_url,
			weight=self.weight,
			aliases=list(self.aliases),
			sources=list(self.sources),
		)

	def to_payload(self) -> dict[str, Any]:
		return {
			"key": self.key,
			"name": self.name,
			"slug": self.slug,
			"twitch_id": self.twitch_id,
			"box_art_url": self.box_art_url,
			"weight": self.weight,
			"aliases": sorted({a for a in self.aliases if a}),
			"sources": sorted({s for s in self.sources if s}),
		}

	@classmethod
	def from_payload(cls, data: dict[str, Any]) -> "GameEntry":
		aliases = data.get("aliases")
		sources = data.get("sources")
		entry = cls(
			key=str(data.get("key") or ""),
			name=str(data.get("name") or ""),
			slug=(data.get("slug") or None),
			twitch_id=(str(data.get("twitch_id")) if data.get("twitch_id") else None),
			box_art_url=data.get("box_art_url"),
			weight=int(data.get("weight") or 0),
			aliases=list({str(a) for a in aliases if a}) if isinstance(aliases, list) else [],
			sources=list({str(s) for s in sources if s}) if isinstance(sources, list) else [],
		)
		return entry


class GameCatalog:
	"""Thread-safe cache of game metadata sourced from Helix + campaign history."""

	def __init__(self, path: str = "data/game_catalog.json") -> None:
		self.path = path
		self._lock = Lock()
		self._games: dict[str, GameEntry] = {}
		self._alias_map: dict[str, str] = {}
		self._ready_event: asyncio.Event = asyncio.Event()
		self._load()

	# ------------------------------------------------------------------ #
	# Internal helpers
	# ------------------------------------------------------------------ #

	def _normalize_entry(self, entry: GameEntry) -> GameEntry:
		entry.key = _norm(entry.key or entry.name)
		entry.name = entry.name.strip() or entry.key
		entry.aliases = sorted({
			a for a in (
				*entry.aliases,
				entry.slug or "",
				entry.key,
			)
			if a
		})
		entry.aliases = [
			alias for alias in {_norm(a) for a in entry.aliases}
			if alias and alias != entry.key
		]
		entry.sources = sorted({s for s in entry.sources if s})
		return entry

	def _load(self) -> None:
		try:
			with open(self.path, "r", encoding="utf-8") as f:
				raw = json.load(f)
		except FileNotFoundError:
			return
		except Exception:
			return
		games = raw.get("games") if isinstance(raw, dict) else None
		if not isinstance(games, list):
			return
		loaded: dict[str, GameEntry] = {}
		for item in games:
			if not isinstance(item, dict):
				continue
			entry = GameEntry.from_payload(item)
			if not entry.name:
				continue
			entry = self._normalize_entry(entry)
			loaded[entry.key] = entry
		with self._lock:
			self._games = loaded
			self._rebuild_alias_map_locked()

	def reset(self) -> None:
		"""Clear any cached games so the cache can be rebuilt."""
		with self._lock:
			self._games = {}
			self._alias_map = {}
			self._ready_event = asyncio.Event()
			tmp = f"{self.path}.tmp"
			os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
			with open(tmp, "w", encoding="utf-8") as f:
				json.dump({"games": []}, f, indent=2, ensure_ascii=False)
			os.replace(tmp, self.path)

	def _rebuild_alias_map_locked(self) -> None:
		alias_map: dict[str, str] = {}
		for key, entry in self._games.items():
			alias_map[key] = key
			for alias in entry.aliases:
				alias_map[alias] = key
		self._alias_map = alias_map

	def _save_locked(self) -> None:
		os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
		payload = {
			"games": [
				e.copy().to_payload()
				for e in sorted(
					self._games.values(),
					key=lambda item: (-item.weight, item.name.casefold(), item.key),
				)
			]
		}
		tmp = f"{self.path}.tmp"
		with open(tmp, "w", encoding="utf-8") as f:
			json.dump(payload, f, indent=2, ensure_ascii=False)
		os.replace(tmp, self.path)

	# ------------------------------------------------------------------ #
	# Public API
	# ------------------------------------------------------------------ #

	def normalize(self, value: str) -> str:
		return _norm(value)

	def count(self) -> int:
		with self._lock:
			return len(self._games)

	def set_ready(self, ready: bool = True) -> None:
		if ready:
			self._ready_event.set()
		else:
			self._ready_event = asyncio.Event()

	def is_ready(self) -> bool:
		return self._ready_event.is_set()

	async def wait_ready(self, timeout: float | None = None) -> bool:
		if self._ready_event.is_set():
			return True
		try:
			if timeout is None:
				await self._ready_event.wait()
			else:
				await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
			return True
		except asyncio.TimeoutError:
			return False

	def merge_games(self, entries: Iterable[GameEntry]) -> bool:
		changed = False
		if not entries:
			return False
		with self._lock:
			for entry in entries:
				if entry is None or not entry.name:
					continue
				entry = self._normalize_entry(entry)
				current = self._games.get(entry.key)
				if current is None:
					self._games[entry.key] = entry.copy()
					changed = True
					continue
				if self._merge_entry_locked(current, entry):
					changed = True
			if changed:
				self._rebuild_alias_map_locked()
				self._save_locked()
		return changed

	def _merge_entry_locked(self, current: GameEntry, incoming: GameEntry) -> bool:
		updated = False
		if incoming.weight > current.weight:
			current.weight = incoming.weight
			updated = True
		if incoming.name and incoming.name != current.name:
			if len(incoming.name) > len(current.name):
				current.name = incoming.name
				updated = True
		if incoming.slug and not current.slug:
			current.slug = incoming.slug
			updated = True
		if incoming.twitch_id and not current.twitch_id:
			current.twitch_id = incoming.twitch_id
			updated = True
		if incoming.box_art_url and not current.box_art_url:
			current.box_art_url = incoming.box_art_url
			updated = True
		combined_aliases = set(current.aliases)
		for alias in incoming.aliases:
			alias = _norm(alias)
			if alias and alias != current.key and alias not in combined_aliases:
				combined_aliases.add(alias)
				updated = True
		current.aliases = sorted(combined_aliases)
		combined_sources = set(current.sources)
		for src in incoming.sources:
			if src and src not in combined_sources:
				combined_sources.add(src)
				updated = True
		current.sources = sorted(combined_sources)
		return updated

	def get(self, value: str) -> Optional[GameEntry]:
		if not value:
			return None
		key = self.normalize(value)
		with self._lock:
			resolved = self._alias_map.get(key)
			if resolved is None:
				resolved = self._alias_map.get(value)
			if resolved is None:
				return None
			entry = self._games.get(resolved)
			return entry.copy() if entry else None

	def get_all(self) -> list[GameEntry]:
		with self._lock:
			return [
				entry.copy()
				for entry in sorted(
					self._games.values(),
					key=lambda item: (-item.weight, item.name.casefold(), item.key),
				)
			]

	def search(self, query: Optional[str], *, limit: int = 25) -> list[GameEntry]:
		normalized = self.normalize(query or "")
		with self._lock:
			entries = list(self._games.values())
		scored: list[tuple[float, GameEntry]] = []
		if not normalized:
			for entry in entries:
				scored.append((float(entry.weight), entry))
		else:
			for entry in entries:
				match_strength = 0.0
				for alias in (entry.key, *entry.aliases):
					if alias == normalized:
						match_strength = max(match_strength, 500.0)
					elif alias.startswith(normalized):
						match_strength = max(match_strength, 320.0)
					elif normalized in alias:
						match_strength = max(match_strength, 180.0)
				if match_strength <= 0.0:
					continue
				score = float(entry.weight) + match_strength
				scored.append((score, entry))
		if not scored:
			return []
		scored.sort(key=lambda item: (-item[0], item[1].name.casefold(), item[1].key))
		return [entry.copy() for _, entry in scored[:limit]]

	def matches_campaign(self, entry: GameEntry, campaign: CampaignRecord) -> bool:
		target_keys = {entry.key, *entry.aliases}
		name_key = self.normalize(campaign.game_name or "")
		slug_key = self.normalize(campaign.game_slug or "")
		for candidate in (name_key, slug_key):
			if candidate and candidate in target_keys:
				return True
			if candidate and candidate in self._alias_map:
				return self._alias_map[candidate] == entry.key
		return False

	def merge_from_campaign_records(self, campaigns: Iterable[CampaignRecord]) -> bool:
		entries: list[GameEntry] = []
		for rec in campaigns:
			name = (rec.game_name or "").strip()
			slug = rec.game_slug or None
			if not name and not slug:
				continue
			entry = GameEntry(
				key=self.normalize(name or slug or ""),
				name=name or slug or "Twitch Game",
				slug=slug,
				twitch_id=None,
				box_art_url=rec.game_box_art,
				weight=700,
				aliases=[self.normalize(name or slug or "")],
				sources=["campaign"],
			)
			if slug:
				entry.aliases.append(self.normalize(slug))
			entries.append(entry)
		return self.merge_games(entries)

	def merge_state_snapshot(self, snapshot: dict[str, Any]) -> bool:
		entries: list[GameEntry] = []
		for item in snapshot.values():
			if not isinstance(item, dict):
				continue
			name = str(item.get("game_name") or "").strip()
			if not name:
				continue
			entry = GameEntry(
				key=self.normalize(name),
				name=name,
				slug=None,
				twitch_id=None,
				box_art_url=item.get("game_box_art"),
				weight=350,
				aliases=[self.normalize(name)],
				sources=["history"],
			)
			entries.append(entry)
		return self.merge_games(entries)

	def merge_state_file(self, path: str) -> bool:
		try:
			with open(path, "r", encoding="utf-8") as f:
				data = json.load(f)
		except FileNotFoundError:
			return False
		except Exception:
			return False
		if not isinstance(data, dict):
			return False
		return self.merge_state_snapshot(data)

	async def refresh_top_games(self, *, max_pages: int | None = None) -> int:
		client_id = os.getenv("TWITCH_HELIX_CLIENT_ID") or os.getenv("TWITCH_CLIENT_ID") or ANDROID_CLIENT_ID
		url = "https://api.twitch.tv/helix/games/top"
		limit_pages = max_pages if max_pages is not None else 20
		total = 0
		page = 0
		entries: list[GameEntry] = []

		try:
			async with aiohttp.ClientSession() as session:
				token = await ensure_env_access_token(session)
				after: Optional[str] = None
				while True:
					params = {"first": "100"}
					if after:
						params["after"] = after
					headers = {
						"Client-ID": client_id,
						"Authorization": f"Bearer {token}",
						"Accept": "application/json",
					}
					async with session.get(url, headers=headers, params=params) as resp:
						text = await resp.text()
						if resp.status >= 400:
							raise GameCatalogUnavailableError(f"{resp.status} {text or 'Failed to fetch Twitch top games'}")
						try:
							payload = await resp.json()
						except Exception as exc:
							raise GameCatalogUnavailableError(f"Invalid JSON from Helix games/top: {exc}") from exc
					data = payload.get("data") if isinstance(payload, dict) else None
					if not isinstance(data, list) or not data:
						break
					for rank_offset, item in enumerate(data):
						if not isinstance(item, dict):
							continue
						name = str(item.get("name") or "").strip()
						if not name:
							continue
						twitch_id = str(item.get("id") or "") or None
						box_art = item.get("box_art_url")
						rank = total + rank_offset
						weight = max(1000 - rank, 100)
						entry = GameEntry(
							key=self.normalize(name),
							name=name,
							slug=None,
							twitch_id=twitch_id,
							box_art_url=box_art,
							weight=weight,
							aliases=[self.normalize(name)],
							sources=["helix"],
						)
						entries.append(entry)
					total += len(data)
					pagination = payload.get("pagination") if isinstance(payload, dict) else None
					after = None
					if isinstance(pagination, dict):
						cursor = pagination.get("cursor")
						after = str(cursor) if cursor else None
					page += 1
					if not after or (limit_pages and page >= limit_pages):
						break
		except GameCatalogUnavailableError:
			raise
		except Exception as exc:  # aiohttp errors
			raise GameCatalogUnavailableError(str(exc)) from exc

		self.merge_games(entries)
		return len(entries)


_CATALOG: GameCatalog | None = None


def get_game_catalog() -> GameCatalog:
	global _CATALOG
	if _CATALOG is None:
		cache_path = os.getenv("TWITCH_GAME_CACHE_PATH", "data/game_catalog.json")
		_CATALOG = GameCatalog(cache_path)
	return _CATALOG


async def warm_game_catalog(*, state_path: Optional[str] = None) -> None:
	"""Fetch Helix top games and merge with stored campaign history."""
	catalog = get_game_catalog()
	catalog.reset()
	print("📦 Game cache warm-up starting…")
	initial_count = 0
	if state_path:
		try:
			if catalog.merge_state_file(state_path):
				initial_count = catalog.count()
				print(f"📦 Seeded game cache with {initial_count} games from historical campaigns.")
		except Exception as exc:
			print(f"⚠️ Failed to reuse campaign history for game cache: {exc}")
	try:
		fetched = await catalog.refresh_top_games()
	except GameCatalogUnavailableError as exc:
		print(f"⚠️ Failed to refresh Twitch top games: {exc}")
		fetched = 0
	total = catalog.count()
	helix_only = max(total - initial_count, 0)
	if total > 0:
		catalog.set_ready(True)
		print(
			"📦 Game cache ready: "
			f"{helix_only} from Helix this boot (raw Helix pages reported {fetched}), "
			f"{initial_count} from stored campaigns, total {total} unique games."
		)
	else:
		print("⚠️ Game cache unavailable; commands will remain disabled until caching succeeds.")


@lightbulb.hook(lightbulb.ExecutionSteps.CHECKS, skip_when_failed=True, name="ensure-game-catalog-ready")
async def ensure_game_catalog_ready_hook(
	pipeline: lb_execution.ExecutionPipeline, ctx: lightbulb.Context
) -> None:
	catalog = get_game_catalog()
	if catalog.is_ready():
		return

	awaited = await catalog.wait_ready(timeout=5.0)
	if awaited and catalog.is_ready():
		return

	message = "DropScout is caching Twitch games. Please try again shortly."
	try:
		await ctx.respond(message, ephemeral=True)
	except Exception:
		pass
	raise GameCatalogNotReady("Game catalog is not ready yet.")


def register_game_catalog_handlers(client: lightbulb.Client) -> None:
	"""Register error handlers to silence catalog initialization failures."""

	@client.error_handler(priority=100)
	async def _ignore_catalog_not_ready(exc: lb_exceptions.ExecutionPipelineFailedException) -> bool:
		return any(isinstance(cause, GameCatalogNotReady) for cause in exc.causes)
