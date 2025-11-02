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
	def __init__(self, rest: Any | None = None) -> None:
		self.rest = rest or object()


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


class StubButtonBuilder:
	def __init__(self, style: hikari.ButtonStyle, custom_id: str) -> None:
		self.style = style
		self.custom_id = custom_id
		self.label = ""
		self.disabled = False

	def set_label(self, value: str) -> "StubButtonBuilder":
		self.label = value
		return self

	def build_payload(self) -> dict[str, object]:
		return {
			"type": int(hikari.ComponentType.BUTTON),
			"style": int(self.style),
			"custom_id": self.custom_id,
			"label": self.label,
			"disabled": self.disabled,
		}


class StubSelectOption:
	def __init__(self, label: str, value: str) -> None:
		self.label = label
		self.value = value
		self.description: str | None = None

	def set_description(self, description: str) -> "StubSelectOption":
		self.description = description
		return self

	def build_payload(self) -> dict[str, object]:
		data: dict[str, object] = {"label": self.label, "value": self.value}
		if self.description is not None:
			data["description"] = self.description
		return data


class StubSelectMenuBuilder:
	def __init__(self, custom_id: str) -> None:
		self.custom_id = custom_id
		self.placeholder = ""
		self.min_values = 1
		self.max_values = 1
		self.options: list[StubSelectOption] = []

	def set_placeholder(self, value: str) -> "StubSelectMenuBuilder":
		self.placeholder = value
		return self

	def set_min_values(self, value: int) -> "StubSelectMenuBuilder":
		self.min_values = value
		return self

	def set_max_values(self, value: int) -> "StubSelectMenuBuilder":
		self.max_values = value
		return self

	def add_option(self, label: str, value: str) -> StubSelectOption:
		option = StubSelectOption(label, value)
		self.options.append(option)
		return option

	def build_payload(self) -> dict[str, object]:
		return {
			"type": int(hikari.ComponentType.TEXT_SELECT_MENU),
			"custom_id": self.custom_id,
			"placeholder": self.placeholder,
			"min_values": self.min_values,
			"max_values": self.max_values,
			"options": [opt.build_payload() for opt in self.options],
		}


class StubActionRowBuilder:
	def __init__(self) -> None:
		self.components: list[object] = []

	def add_button(self, style: hikari.ButtonStyle, custom_id: str) -> StubButtonBuilder:
		button = StubButtonBuilder(style, custom_id)
		self.components.append(button)
		return button

	def add_text_select_menu(self, custom_id: str) -> StubSelectMenuBuilder:
		menu = StubSelectMenuBuilder(custom_id)
		self.components.append(menu)
		return menu

	def build(self) -> Tuple[dict[str, object], Sequence[hikari.files.Resourceish]]:
		payload = {
			"type": int(hikari.ComponentType.ACTION_ROW),
			"components": [comp.build_payload() for comp in self.components],
		}
		return payload, ()


class StubRest:
	def build_message_action_row(self) -> StubActionRowBuilder:
		return StubActionRowBuilder()


@pytest.mark.asyncio
async def test_component_handler_removes_selected_favorites(monkeypatch):
	campaigns = [_campaign("blue-archive", ends_hours=24)]
	shared = DummyShared(campaigns)
	shared.favorites_store.set_user_favorites(1, 5, ["blue-archive"])

	client = FakeClient()
	fav_mod.register(client, shared)
	handler = client.listeners[hikari.InteractionCreateEvent]

	class _Marker:
		pass

	monkeypatch.setattr(hikari, "ComponentInteraction", _Marker)

	class DummyInteraction(_Marker):
		def __init__(self) -> None:
			self.custom_id = fav_mod.REMOVE_SELECT_ID
			self.guild_id = 1
			self.user = type("User", (), {"id": 5})()
			self.app = DummyApp(rest=StubRest())
			self.values = ["blue-archive"]
			self.responses: list[tuple[hikari.ResponseType, dict[str, object]]] = []

		async def create_initial_response(self, response_type, **kwargs):
			self.responses.append((response_type, kwargs))

	interaction = DummyInteraction()
	event = hikari.InteractionCreateEvent(shard=None, interaction=interaction)

	await handler(event)

	assert not shared.favorites_store.get_user_favorites(1, 5)
	assert interaction.responses, "Expected removal to update message"
	response_type, payload = interaction.responses[0]
	assert response_type == hikari.ResponseType.MESSAGE_UPDATE
	assert payload["content"] == "Selected favorites removed."
	row_payload, _ = payload["components"][0].build()
	assert row_payload["components"][0]["custom_id"] == fav_mod.REFRESH_BUTTON_ID


@pytest.mark.asyncio
async def test_component_handler_refreshes_overview(monkeypatch):
	campaigns = [_campaign("blue-archive", ends_hours=24)]
	shared = DummyShared(campaigns)
	shared.favorites_store.set_user_favorites(2, 7, ["blue-archive"])

	client = FakeClient()
	fav_mod.register(client, shared)
	handler = client.listeners[hikari.InteractionCreateEvent]

	class _Marker:
		pass

	monkeypatch.setattr(hikari, "ComponentInteraction", _Marker)

	class DummyInteraction(_Marker):
		def __init__(self) -> None:
			self.custom_id = fav_mod.REFRESH_BUTTON_ID
			self.guild_id = 2
			self.user = type("User", (), {"id": 7})()
			self.app = DummyApp(rest=StubRest())
			self.values: list[str] = []
			self.responses: list[tuple[hikari.ResponseType, dict[str, object]]] = []

		async def create_initial_response(self, response_type, **kwargs):
			self.responses.append((response_type, kwargs))

	interaction = DummyInteraction()
	event = hikari.InteractionCreateEvent(shard=None, interaction=interaction)

	await handler(event)

	assert interaction.responses, "Expected refresh to update message"
	response_type, payload = interaction.responses[0]
	assert response_type == hikari.ResponseType.MESSAGE_UPDATE
	embeds = payload.get("embeds") or []
	assert embeds and embeds[0].title == "Favorite Games"


@pytest.mark.asyncio
async def test_component_handler_ignores_unrelated_component(monkeypatch):
	shared = DummyShared([])
	client = FakeClient()
	fav_mod.register(client, shared)
	handler = client.listeners[hikari.InteractionCreateEvent]

	class _Marker:
		pass

	monkeypatch.setattr(hikari, "ComponentInteraction", _Marker)

	class DummyInteraction(_Marker):
		def __init__(self) -> None:
			self.custom_id = "drops:fav:noop"
			self.guild_id = 1
			self.user = type("User", (), {"id": 1})()
			self.app = DummyApp()
			self.responses: list[tuple[hikari.ResponseType, dict[str, Any]]] = []
			self.values: list[str] = []

		async def create_initial_response(self, response_type, **kwargs):
			self.responses.append((response_type, kwargs))

	interaction = DummyInteraction()
	event = hikari.InteractionCreateEvent(shard=None, interaction=interaction)

	await handler(event)

	assert interaction.responses == []


@pytest.mark.asyncio
async def test_component_handler_prevents_cross_user_control(monkeypatch):
	campaigns = [_campaign("blue-archive", ends_hours=24)]
	shared = DummyShared(campaigns)
	shared.favorites_store.set_user_favorites(1, 5, ["blue-archive"])

	client = FakeClient()
	fav_mod.register(client, shared)
	handler = client.listeners[hikari.InteractionCreateEvent]

	class _Marker:
		pass

	monkeypatch.setattr(hikari, "ComponentInteraction", _Marker)

	class DummyInteraction(_Marker):
		def __init__(self) -> None:
			self.custom_id = f"{fav_mod.CHECK_GOTO_ID}:99:0"
			self.guild_id = 1
			self.user = type("User", (), {"id": 5})()
			self.app = DummyApp(rest=StubRest())
			self.responses: list[tuple[hikari.ResponseType, dict[str, Any]]] = []
			self.values: list[str] = []

		async def create_initial_response(self, response_type, **kwargs):
			self.responses.append((response_type, kwargs))

	interaction = DummyInteraction()
	event = hikari.InteractionCreateEvent(shard=None, interaction=interaction)

	await handler(event)

	assert interaction.responses, "Expected warning message"
	response_type, payload = interaction.responses[0]
	assert response_type == hikari.ResponseType.MESSAGE_CREATE
	assert payload["flags"] == hikari.MessageFlag.EPHEMERAL
	assert "cannot control another user's favorites" in payload["content"]


@pytest.mark.asyncio
async def test_component_handler_remove_many_no_changes(monkeypatch):
	campaigns = [_campaign("blue-archive", ends_hours=24)]
	shared = DummyShared(campaigns)
	shared.favorites_store.set_user_favorites(1, 5, ["blue-archive"])

	client = FakeClient()
	fav_mod.register(client, shared)
	handler = client.listeners[hikari.InteractionCreateEvent]

	class _Marker:
		pass

	monkeypatch.setattr(hikari, "ComponentInteraction", _Marker)

	class DummyInteraction(_Marker):
		def __init__(self) -> None:
			self.custom_id = fav_mod.REMOVE_SELECT_ID
			self.guild_id = 1
			self.user = type("User", (), {"id": 5})()
			self.app = DummyApp(rest=StubRest())
			self.responses: list[tuple[hikari.ResponseType, dict[str, Any]]] = []
			self.values = ["non-existent"]

		async def create_initial_response(self, response_type, **kwargs):
			self.responses.append((response_type, kwargs))

	interaction = DummyInteraction()
	event = hikari.InteractionCreateEvent(shard=None, interaction=interaction)

	await handler(event)

	assert interaction.responses, "Expected update response"
	_, payload = interaction.responses[0]
	assert payload["content"] == "Those games were not in your favorites."


@pytest.mark.asyncio
async def test_component_handler_pagination_fetch_failure(monkeypatch):
	campaigns = [_campaign("blue-archive", ends_hours=24)]
	shared = DummyShared(campaigns)
	shared.favorites_store.set_user_favorites(1, 5, ["blue-archive"])

	client = FakeClient()
	fav_mod.register(client, shared)
	handler = client.listeners[hikari.InteractionCreateEvent]

	class _Marker:
		pass

	monkeypatch.setattr(hikari, "ComponentInteraction", _Marker)

	class DummyInteraction(_Marker):
		def __init__(self) -> None:
			self.custom_id = f"{fav_mod.CHECK_GOTO_ID}:5:0"
			self.guild_id = 1
			self.user = type("User", (), {"id": 5})()
			self.app = DummyApp(rest=StubRest())
			self.responses: list[tuple[hikari.ResponseType, dict[str, Any]]] = []
			self.values: list[str] = []

		async def create_initial_response(self, response_type, **kwargs):
			self.responses.append((response_type, kwargs))

	interaction = DummyInteraction()

	async def boom():
		raise RuntimeError("boom")

	monkeypatch.setattr(shared, "get_campaigns_cached", boom)

	event = hikari.InteractionCreateEvent(shard=None, interaction=interaction)
	await handler(event)

	assert interaction.responses, "Expected failure response"
	response_type, payload = interaction.responses[0]
	assert response_type == hikari.ResponseType.MESSAGE_UPDATE
	assert payload["content"] == "Failed to refresh favorites."


@pytest.mark.asyncio
async def test_component_handler_pagination_no_active_campaigns(monkeypatch):
	campaigns = []
	shared = DummyShared(campaigns)
	shared.favorites_store.set_user_favorites(1, 5, ["blue-archive"])

	client = FakeClient()
	fav_mod.register(client, shared)
	handler = client.listeners[hikari.InteractionCreateEvent]

	class _Marker:
		pass

	monkeypatch.setattr(hikari, "ComponentInteraction", _Marker)

	class DummyInteraction(_Marker):
		def __init__(self) -> None:
			self.custom_id = f"{fav_mod.CHECK_GOTO_ID}:5:0"
			self.guild_id = 1
			self.user = type("User", (), {"id": 5})()
			self.app = DummyApp(rest=StubRest())
			self.responses: list[tuple[hikari.ResponseType, dict[str, Any]]] = []
			self.values: list[str] = []

		async def create_initial_response(self, response_type, **kwargs):
			self.responses.append((response_type, kwargs))

	async def fake_campaigns():
		return []

	monkeypatch.setattr(shared, "get_campaigns_cached", fake_campaigns)

	interaction = DummyInteraction()
	event = hikari.InteractionCreateEvent(shard=None, interaction=interaction)

	await handler(event)

	assert interaction.responses, "Expected empty favorites response"
	response_type, payload = interaction.responses[0]
	assert response_type == hikari.ResponseType.MESSAGE_UPDATE
	assert payload["content"] == "No active campaigns for your favorites right now."


@pytest.mark.asyncio
async def test_component_handler_invalid_custom_id_format(monkeypatch):
	shared = DummyShared([])
	client = FakeClient()
	fav_mod.register(client, shared)
	handler = client.listeners[hikari.InteractionCreateEvent]

	class _Marker:
		pass

	monkeypatch.setattr(hikari, "ComponentInteraction", _Marker)

	class DummyInteraction(_Marker):
		def __init__(self) -> None:
			self.custom_id = f"{fav_mod.CHECK_GOTO_ID}:too:few"
			self.guild_id = 1
			self.user = type("User", (), {"id": 1})()
			self.app = DummyApp()
			self.responses: list[tuple[hikari.ResponseType, dict[str, Any]]] = []
			self.values: list[str] = []

		async def create_initial_response(self, response_type, **kwargs):
			self.responses.append((response_type, kwargs))

	interaction = DummyInteraction()
	event = hikari.InteractionCreateEvent(shard=None, interaction=interaction)

	await handler(event)

	assert interaction.responses == []


@pytest.mark.asyncio
async def test_component_handler_requires_guild_and_user(monkeypatch):
	shared = DummyShared([])
	client = FakeClient()
	fav_mod.register(client, shared)
	handler = client.listeners[hikari.InteractionCreateEvent]

	class _Marker:
		pass

	monkeypatch.setattr(hikari, "ComponentInteraction", _Marker)

	class NoGuildInteraction(_Marker):
		def __init__(self) -> None:
			self.custom_id = fav_mod.REMOVE_SELECT_ID
			self.guild_id = None
			self.user = type("User", (), {"id": 1})()
			self.app = DummyApp(rest=StubRest())
			self.values: list[str] = []
			self.responses: list[tuple[hikari.ResponseType, dict[str, Any]]] = []

		async def create_initial_response(self, response_type, **kwargs):
			self.responses.append((response_type, kwargs))

	interaction = NoGuildInteraction()
	event = hikari.InteractionCreateEvent(shard=None, interaction=interaction)

	await handler(event)

	assert interaction.responses, "Expected guild guard response"
	response_type, payload = interaction.responses[0]
	assert response_type == hikari.ResponseType.MESSAGE_UPDATE
	assert payload["content"] == "Favorites can only be managed inside a server."

	class NoUserInteraction(_Marker):
		def __init__(self) -> None:
			self.custom_id = fav_mod.REMOVE_SELECT_ID
			self.guild_id = 1
			self.user = None
			self.app = DummyApp(rest=StubRest())
			self.values: list[str] = []
			self.responses: list[tuple[hikari.ResponseType, dict[str, Any]]] = []

		async def create_initial_response(self, response_type, **kwargs):
			self.responses.append((response_type, kwargs))

	interaction2 = NoUserInteraction()
	event2 = hikari.InteractionCreateEvent(shard=None, interaction=interaction2)

	await handler(event2)

	assert interaction2.responses, "Expected user guard response"
	response_type2, payload2 = interaction2.responses[0]
	assert response_type2 == hikari.ResponseType.MESSAGE_UPDATE
	assert payload2["content"] == "Favorites can only be managed inside a server."
