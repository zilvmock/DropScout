from __future__ import annotations

"""Persistence helpers for per-user favorite games.

Favorites are stored per guild and keyed by the normalized game key from the
catalog. The store is JSON-backed and guarded by a process-wide lock to keep
updates atomic even when multiple commands touch favorites concurrently.
"""

import json
import os
from threading import Lock
from typing import Iterable

_FAVORITES_LOCK = Lock()


class FavoritesStore:
	"""JSON-backed store for user favorite games per guild."""

	def __init__(self, path: str = "data/favorites.json") -> None:
		self.path = path

	def _load_unlocked(self) -> dict[str, dict[str, list[str]]]:
		try:
			with open(self.path, "r", encoding="utf-8") as fh:
				data = json.load(fh)
		except FileNotFoundError:
			return {}
		except Exception:
			return {}

		if not isinstance(data, dict):
			return {}

		result: dict[str, dict[str, list[str]]] = {}
		for guild_id, users in data.items():
			if not isinstance(users, dict):
				continue
			guild_map: dict[str, list[str]] = {}
			for user_id, favorites in users.items():
				if not isinstance(favorites, list):
					continue
				unique = []
				seen: set[str] = set()
				for item in favorites:
					if not isinstance(item, str):
						continue
					key = item.strip()
					if not key or key in seen:
						continue
					seen.add(key)
					unique.append(key)
				if unique:
					guild_map[str(user_id)] = unique
			if guild_map:
				result[str(guild_id)] = guild_map
		return result

	def _atomic_write(self, payload: str) -> None:
		dirname = os.path.dirname(self.path) or "."
		os.makedirs(dirname, exist_ok=True)
		tmp = f"{self.path}.tmp"
		with open(tmp, "w", encoding="utf-8") as fh:
			fh.write(payload)
		os.replace(tmp, self.path)

	def _save_locked(self, data: dict[str, dict[str, list[str]]]) -> None:
		payload = json.dumps(data, indent=2, ensure_ascii=False)
		self._atomic_write(payload)

	def load(self) -> dict[str, dict[str, list[str]]]:
		with _FAVORITES_LOCK:
			return self._load_unlocked()

	def add_favorite(self, guild_id: int, user_id: int, game_key: str) -> bool:
		game_key = (game_key or "").strip()
		if not game_key:
			return False
		changed = False
		with _FAVORITES_LOCK:
			data = self._load_unlocked()
			guild_key = str(guild_id)
			user_key = str(user_id)
			guild_map = data.get(guild_key, {})
			current = guild_map.get(user_key, [])
			if game_key not in current:
				current = sorted({*current, game_key})
				guild_map[user_key] = current
				data[guild_key] = guild_map
				self._save_locked(data)
				changed = True
		return changed

	def remove_favorite(self, guild_id: int, user_id: int, game_key: str) -> bool:
		game_key = (game_key or "").strip()
		if not game_key:
			return False
		changed = False
		with _FAVORITES_LOCK:
			data = self._load_unlocked()
			guild_key = str(guild_id)
			user_key = str(user_id)
			guild_map = data.get(guild_key)
			if not guild_map:
				return False
			current = guild_map.get(user_key, [])
			if game_key not in current:
				return False
			current = [item for item in current if item != game_key]
			if current:
				guild_map[user_key] = current
			else:
				guild_map.pop(user_key, None)
			if not guild_map:
				data.pop(guild_key, None)
			else:
				data[guild_key] = guild_map
			self._save_locked(data)
			changed = True
		return changed

	def remove_many(self, guild_id: int, user_id: int, game_keys: Iterable[str]) -> int:
		keys = {item.strip() for item in game_keys if item and item.strip()}
		if not keys:
			return 0
		removed = 0
		with _FAVORITES_LOCK:
			data = self._load_unlocked()
			guild_key = str(guild_id)
			user_key = str(user_id)
			guild_map = data.get(guild_key)
			if not guild_map:
				return 0
			current = guild_map.get(user_key, [])
			if not current:
				return 0
			new_items = [item for item in current if item not in keys]
			removed = len(current) - len(new_items)
			if new_items:
				guild_map[user_key] = new_items
			else:
				guild_map.pop(user_key, None)
			if not guild_map:
				data.pop(guild_key, None)
			else:
				data[guild_key] = guild_map
			if removed:
				self._save_locked(data)
		return removed

	def get_user_favorites(self, guild_id: int, user_id: int) -> list[str]:
		data = self.load()
		guild_map = data.get(str(guild_id), {})
		items = guild_map.get(str(user_id), [])
		return list(items)

	def get_guild_favorites(self, guild_id: int) -> dict[int, set[str]]:
		data = self.load()
		guild_map = data.get(str(guild_id), {})
		result: dict[int, set[str]] = {}
		for user_id, items in guild_map.items():
			try:
				uid = int(user_id)
			except ValueError:
				continue
			result[uid] = {item for item in items if item}
		return result

	def get_watchers(self, guild_id: int, keys: Iterable[str]) -> dict[int, set[str]]:
		target_keys = {item.strip() for item in keys if item}
		if not target_keys:
			return {}
		guild_map = self.get_guild_favorites(guild_id)
		result: dict[int, set[str]] = {}
		for uid, games in guild_map.items():
			match = games & target_keys
			if match:
				result[uid] = match
		return result
