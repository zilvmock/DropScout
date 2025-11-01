from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import hikari
import pytest

from functionality.twitch_drops.commands import favorites as fav_mod
from functionality.twitch_drops.models import BenefitRecord, CampaignRecord


class FakeClient:
	def __init__(self) -> None:
		self.registered: list[Any] = []
		self.listeners: Dict[Any, Any] = {}

	def register(self, obj: Any) -> Any:
		self.registered.append(obj)
		return obj

	def listen(self, event: Any):
		def decorator(fn):
			self.listeners[event] = fn
			return fn

		return decorator


@dataclass
class DummyEntry:
	key: str
	name: str
	slug: str


class DummyCatalog:
	def __init__(self, entries: Dict[str, Tuple[str, str]]) -> None:
		self._entries = entries

	def get(self, key: str) -> Optional[DummyEntry]:
		data = self._entries.get(key)
		if not data:
			return None
		name, slug = data
		return DummyEntry(key=key, name=name, slug=slug)

	def matches_campaign(self, entry: DummyEntry, record: CampaignRecord) -> bool:
		slug = (record.game_slug or "").casefold()
		return slug == entry.slug


class DummyFavoritesStore:
	def __init__(self) -> None:
		self._data: Dict[Tuple[int, int], List[str]] = {}

	def set_user_favorites(self, guild_id: int, user_id: int, values: Iterable[str]) -> None:
		self._data[(guild_id, user_id)] = list(values)

	def get_user_favorites(self, guild_id: int, user_id: int) -> List[str]:
		return list(self._data.get((guild_id, user_id), []))

	def remove_many(self, guild_id: int, user_id: int, values: Sequence[str]) -> bool:
		key = (guild_id, user_id)
		current = self._data.get(key, [])
		initial = set(current)
		current = [item for item in current if item not in values]
		self._data[key] = current
		return set(current) != initial


class DummyShared:
	def __init__(self, campaigns: List[CampaignRecord]) -> None:
		self.guild_store = object()
		self.MAX_ATTACH_PER_CMD = 0
		self.SEND_DELAY_MS = 0
		self.ICON_LIMIT = 9
		self.ICON_COLUMNS = 3
		self.ICON_SIZE = 96
		self.FETCH_TTL = 120
		self._campaigns = campaigns
		self.favorites_store = DummyFavoritesStore()
		self.game_catalog = DummyCatalog(
			{
				"blue-archive": ("Blue Archive", "blue-archive"),
				"helldivers-2": ("Helldivers 2", "helldivers-2"),
			}
		)
		self.finalized_messages: list[Optional[str]] = []

	async def get_campaigns_cached(self) -> List[CampaignRecord]:
		return list(self._campaigns)

	async def finalize_interaction(self, ctx: Any, *, message: Optional[str] = None) -> None:
		self.finalized_messages.append(message)


class DummyApp:
	def __init__(self) -> None:
		self.rest = object()


class DummyCtx:
	def __init__(self, app: DummyApp, guild_id: int, user_id: int) -> None:
		self.client = type("Client", (), {"app": app})()
		self.guild_id = guild_id
		self.channel_id = 1234
		self.user = type("User", (), {"id": user_id})()
		self.deferred = False
		self.responses: list[dict[str, Any]] = []
		self.edited_payload: Optional[dict[str, Any]] = None

	async def defer(self, ephemeral: bool = False) -> None:
		self.deferred = True

	async def respond(self, **kwargs: Any) -> None:
		self.responses.append(kwargs)

	async def edit_initial_response(self, **kwargs: Any) -> None:
		self.edited_payload = kwargs


def _campaign(slug: str, *, ends_hours: int) -> CampaignRecord:
	now = datetime.now(timezone.utc)
	return CampaignRecord(
		id=f"{slug}-{ends_hours}",
		name=f"{slug.title()} Campaign",
		status="ACTIVE",
		game_name=slug.title(),
		game_slug=slug,
		game_box_art=None,
		starts_at=now.isoformat(),
		ends_at=(now + timedelta(hours=ends_hours)).isoformat(),
		benefits=[BenefitRecord(id="b1", name="Reward", image_url="https://example.com/reward.png")],
	)


@pytest.mark.asyncio
async def test_build_check_payload_includes_prev_next_buttons():
	campaigns = [_campaign("blue-archive", ends_hours=24), _campaign("helldivers-2", ends_hours=12)]
	shared = DummyShared(campaigns)
	shared.favorites_store.set_user_favorites(1, 42, ["blue-archive", "helldivers-2"])

	favs = shared.favorites_store.get_user_favorites(1, 42)
	pages = fav_mod._build_favorite_pages(shared, favs, campaigns)
	assert len(pages) == 2

	content, embeds, components = fav_mod._build_check_page_payload(DummyApp(), 42, pages, 0)
	assert "Blue Archive" in content
	assert embeds, "Expected at least one embed"
	assert components, "Expected paginator components"
	payload, attachments = components[0].build()
	assert payload["components"][0]["label"] == "Previous"
	assert payload["components"][0]["disabled"] is True
	assert payload["components"][1]["disabled"] is False
	assert attachments == ()


@pytest.mark.asyncio
async def test_check_command_produces_paginated_response():
	campaigns = [_campaign("blue-archive", ends_hours=48), _campaign("helldivers-2", ends_hours=24)]
	shared = DummyShared(campaigns)
	shared.favorites_store.set_user_favorites(1, 99, ["blue-archive", "helldivers-2"])

	client = FakeClient()
	group_name = fav_mod.register(client, shared)
	assert group_name == "drops_favorites"
	group = client.registered[0]
	check_cls = group.subcommands["check"]

	ctx = DummyCtx(DummyApp(), guild_id=1, user_id=99)
	# Bind the invoke coroutine
	bound_invoke = check_cls.invoke.__get__(object.__new__(check_cls), check_cls)

	await bound_invoke(ctx)

	payload = ctx.edited_payload or (ctx.responses[-1] if ctx.responses else None)
	assert payload, "Expected command to send a response"
	assert payload["components"], "Expected paginator components in response"
	row_payload, _ = payload["components"][0].build()
	assert row_payload["components"][1]["disabled"] is False


@pytest.mark.asyncio
async def test_component_handler_advances_page(monkeypatch):
	campaigns = [_campaign("blue-archive", ends_hours=48), _campaign("helldivers-2", ends_hours=24)]
	shared = DummyShared(campaigns)
	shared.favorites_store.set_user_favorites(1, 10, ["blue-archive", "helldivers-2"])

	client = FakeClient()
	fav_mod.register(client, shared)

	handler = client.listeners[hikari.InteractionCreateEvent]

	class _Marker:
		pass

	monkeypatch.setattr(hikari, "ComponentInteraction", _Marker)

	class DummyInteraction(_Marker):
		def __init__(self) -> None:
			self.custom_id = f"{fav_mod.CHECK_GOTO_ID}:10:1"
			self.guild_id = 1
			self.user = type("User", (), {"id": 10})()
			self.app = DummyApp()
			self.responses: list[tuple[hikari.ResponseType, dict[str, Any]]] = []
			self.values: list[str] = []

		async def create_initial_response(self, response_type, **kwargs):
			self.responses.append((response_type, kwargs))

	interaction = DummyInteraction()

	event = hikari.InteractionCreateEvent(shard=None, interaction=interaction)

	await handler(event)

	assert interaction.responses, "Expected handler to send an updated page"
	response_type, payload = interaction.responses[0]
	assert response_type == hikari.ResponseType.MESSAGE_UPDATE
	row_payload, _ = payload["components"][0].build()
	assert row_payload["components"][0]["disabled"] is False
