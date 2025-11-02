from typing import Sequence

import hikari
import lightbulb
import pytest

from functionality.twitch_drops.commands import favorites as favorites_mod
from functionality.twitch_drops.commands.common import SharedContext
from functionality.twitch_drops.config import GuildConfigStore
from functionality.twitch_drops.favorites import FavoritesStore
from functionality.twitch_drops.game_catalog import GameCatalog, GameEntry
from functionality.twitch_drops.models import BenefitRecord, CampaignRecord


class StubClient:
	def __init__(self) -> None:
		self.registered: list[object] = []

	def register(self, item, *args, **kwargs):
		self.registered.append(item)
		return item


class StubAutocompleteContext:
	def __init__(self, *, focused: str = "", guild_id: int | None = None, user_id: int | None = None) -> None:
		self.focused = type("Focus", (), {"value": focused})()
		self.options: dict[str, str] = {}
		self._choices: list[tuple[str, str]] | None = None
		if guild_id is not None and user_id is not None:
			user = type("User", (), {"id": user_id})()
			self.interaction = type("Interaction", (), {"guild_id": guild_id, "user": user})()
		else:
			self.interaction = None

	async def respond(self, choices):
		self._choices = choices


class StubButtonBuilder:
	def __init__(self, style: hikari.ButtonStyle, custom_id: str) -> None:
		self.style = style
		self.custom_id = custom_id
		self.label: str | None = None
		self.disabled = False

	def set_label(self, label: str) -> "StubButtonBuilder":
		self.label = label
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
		data: dict[str, object] = {
			"label": self.label,
			"value": self.value,
		}
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
			"options": [option.build_payload() for option in self.options],
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

	def build(self) -> tuple[dict[str, object], Sequence[hikari.files.Resourceish]]:
		payload = {
			"type": int(hikari.ComponentType.ACTION_ROW),
			"components": [component.build_payload() for component in self.components],
		}
		return payload, ()


class StubRest:
	def __init__(self, *, fail: bool = False) -> None:
		self.fail = fail

	def build_message_action_row(self) -> StubActionRowBuilder:
		if self.fail:
			raise RuntimeError("rest-unavailable")
		return StubActionRowBuilder()


class StubApp:
	def __init__(self, rest: StubRest | None = None) -> None:
		self.rest = rest or StubRest()


class BaseCtx:
	def __init__(self, *, guild_id: int | None = 123, user_id: int = 99, app: StubApp | None = None) -> None:
		self.guild_id = guild_id
		self.channel_id = 555
		if user_id is not None:
			self.user = type("User", (), {"id": user_id})()
		self.client = type("Client", (), {"app": app or StubApp()})()
		self.respond_calls: list[dict[str, object]] = []
		self.deferred = False
		self.edited_initial: dict[str, object] | None = None

	async def respond(self, *args, **kwargs) -> None:
		payload = dict(kwargs)
		if args:
			payload["content"] = args[0]
		self.respond_calls.append(payload)

	async def defer(self, *args, **kwargs) -> None:
		self.deferred = True

	async def edit_initial_response(self, **kwargs) -> None:
		self.edited_initial = kwargs


class MemberCtx(BaseCtx):
	def __init__(self, *, guild_id: int | None, member_id: int, app: StubApp | None = None) -> None:
		super().__init__(guild_id=guild_id, user_id=None, app=app)
		self.member = type("Member", (), {"id": member_id})()


class AuthorCtx(BaseCtx):
	def __init__(self, *, guild_id: int | None, author_id: int, app: StubApp | None = None) -> None:
		super().__init__(guild_id=guild_id, user_id=None, app=app)
		self.author = type("Author", (), {"id": author_id})()


@pytest.fixture()
def shared(tmp_path) -> SharedContext:
	game_catalog = GameCatalog(str(tmp_path / "catalog.json"))
	favorites_store = FavoritesStore(str(tmp_path / "favorites.json"))
	return SharedContext(
		guild_store=GuildConfigStore(str(tmp_path / "guild.json")),
		ICON_LIMIT=9,
		ICON_SIZE=96,
		ICON_COLUMNS=3,
		MAX_ATTACH_PER_CMD=0,
		SEND_DELAY_MS=0,
		FETCH_TTL=30,
		game_catalog=game_catalog,
		favorites_store=favorites_store,
	)


@pytest.fixture()
def favorites_group(shared: SharedContext):
	client = StubClient()
	name = favorites_mod.register(client, shared)
	assert name == "drops_favorites"
	group = next(item for item in client.registered if isinstance(item, lightbulb.commands.groups.Group))
	return group, shared


def test_favorites_commands_group_structure(favorites_group):
	group, _ = favorites_group
	assert set(group.subcommands.keys()) == {"view", "add", "check", "remove"}

	view_cmd = group.subcommands["view"]
	assert issubclass(view_cmd, lightbulb.SlashCommand)
	assert view_cmd._command_data.options == {}

	add_cmd = group.subcommands["add"]
	assert issubclass(add_cmd, lightbulb.SlashCommand)
	add_option = add_cmd._command_data.options["game"]
	assert getattr(add_option.type, "name", None) == "STRING"
	assert add_option.autocomplete_provider is not hikari.UNDEFINED

	remove_cmd = group.subcommands["remove"]
	assert issubclass(remove_cmd, lightbulb.SlashCommand)
	remove_option = remove_cmd._command_data.options["game"]
	assert getattr(remove_option.type, "name", None) == "STRING"
	assert remove_option.autocomplete_provider is not hikari.UNDEFINED

	check_cmd = group.subcommands["check"]
	assert issubclass(check_cmd, lightbulb.SlashCommand)
	assert check_cmd._command_data.options == {}


@pytest.mark.asyncio
async def test_add_autocomplete_returns_catalog_matches(favorites_group):
	group, shared = favorites_group
	add_option = group.subcommands["add"]._command_data.options["game"]
	provider = add_option.autocomplete_provider

	shared.game_catalog.merge_games(
		[
			GameEntry(key="valorant", name="Valorant", weight=500),
			GameEntry(key="apex", name="Apex Legends", weight=300),
		]
	)
	shared.game_catalog.set_ready(True)

	ctx = StubAutocompleteContext(focused="val")
	await provider(ctx)
	assert ctx._choices == [("Valorant", "valorant")]


@pytest.mark.asyncio
async def test_add_autocomplete_empty_when_catalog_not_ready(favorites_group):
	group, shared = favorites_group
	add_option = group.subcommands["add"]._command_data.options["game"]
	provider = add_option.autocomplete_provider

	shared.game_catalog.set_ready(False)

	ctx = StubAutocompleteContext(focused="anything")
	await provider(ctx)
	assert ctx._choices == []


@pytest.mark.asyncio
async def test_remove_autocomplete_only_user_favorites(favorites_group):
	group, shared = favorites_group
	remove_option = group.subcommands["remove"]._command_data.options["game"]
	provider = remove_option.autocomplete_provider

	shared.favorites_store.add_favorite(123, 1, "valorant")
	shared.favorites_store.add_favorite(123, 1, "apex")
	shared.favorites_store.add_favorite(123, 2, "fortnite")

	ctx = StubAutocompleteContext(focused="ap", guild_id=123, user_id=1)
	await provider(ctx)
	assert ctx._choices == [("apex", "apex")]


@pytest.mark.asyncio
async def test_check_sends_now_active_messages(monkeypatch, favorites_group):
	group, shared = favorites_group
	check_cmd = group.subcommands["check"]
	cmd_instance = object.__new__(check_cmd)

	shared.game_catalog.merge_games(
		[
			GameEntry(key="valorant", name="Valorant", weight=500),
		]
	)
	shared.game_catalog.set_ready(True)
	shared.favorites_store.add_favorite(123, 42, "valorant")

	campaign = CampaignRecord(
		id="camp-1",
		name="Valorant Drops",
		status="ACTIVE",
		game_name="Valorant",
		game_slug="valorant",
		game_box_art=None,
		starts_at=None,
		ends_at=None,
		benefits=[BenefitRecord(id="b1", name="Reward", image_url=None)],
	)

	async def fake_campaigns():
		return [campaign]

	monkeypatch.setattr(shared, "get_campaigns_cached", fake_campaigns)

	class FakeCtx:
		def __init__(self) -> None:
			self.guild_id = 123
			self.channel_id = 999
			self.user = type("User", (), {"id": 42})()
			self.client = type("Client", (), {"app": object()})()
			self.deferred = False
			self.edited_initial: dict | None = None
			self.respond_calls: list[dict] = []

		async def defer(self, *args, **kwargs):
			self.deferred = True

		async def respond(self, **kwargs):
			self.respond_calls.append(kwargs)

		async def edit_initial_response(self, **kwargs):
			self.edited_initial = kwargs

		async def delete_last_response(self, *args, **kwargs):
			return

		async def delete_initial_response(self, *args, **kwargs):
			return

		async def edit_last_response(self, *args, **kwargs):
			return

	ctx = FakeCtx()

	finalized = []

	async def fake_finalize(ctx_obj, *, message=None):
		finalized.append(message)

	monkeypatch.setattr(shared, "finalize_interaction", fake_finalize)

	bound_invoke = check_cmd.invoke.__get__(cmd_instance, check_cmd)
	await bound_invoke(ctx)

	payload = ctx.edited_initial or (ctx.respond_calls[-1] if ctx.respond_calls else None)
	assert payload is not None, "Expected the deferred response to be edited"
	assert payload.get("embeds"), "Expected embeds in payload"
	first_embed = payload["embeds"][0]
	assert first_embed.title and "Valorant" in first_embed.title
	components = payload.get("components")
	if components:
		row_payload, attachments = components[0].build()
		assert row_payload["components"][0]["label"] == "Previous"
		assert attachments == ()
	assert finalized == []


@pytest.mark.asyncio
async def test_view_command_renders_overview_with_refresh(favorites_group):
	group, shared = favorites_group
	view_cmd = group.subcommands["view"]

	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=500)])
	shared.favorites_store.add_favorite(321, 77, "valorant")

	ctx = BaseCtx(guild_id=321, user_id=77)
	bound_invoke = view_cmd.invoke.__get__(object.__new__(view_cmd), view_cmd)

	await bound_invoke(ctx)

	assert ctx.respond_calls, "Expected the command to respond"
	payload = ctx.respond_calls[0]
	assert payload.get("ephemeral") is True
	embeds = payload.get("embeds") or []
	assert embeds, "Expected an overview embed"
	assert embeds[0].title == "Favorite Games"
	assert "Valorant" in (embeds[0].description or "")
	components = payload.get("components") or []
	assert components, "Expected refresh components"
	row_payload, attachments = components[0].build()
	assert row_payload["components"][0]["custom_id"] == favorites_mod.REFRESH_BUTTON_ID
	assert attachments == ()


@pytest.mark.asyncio
async def test_view_command_uses_member_fallback(favorites_group):
	group, shared = favorites_group
	view_cmd = group.subcommands["view"]
	shared.game_catalog.merge_games([GameEntry(key="halo", name="Halo", weight=100)])
	shared.favorites_store.add_favorite(50, 60, "halo")

	ctx = MemberCtx(guild_id=50, member_id=60)
	bound_invoke = view_cmd.invoke.__get__(object.__new__(view_cmd), view_cmd)

	await bound_invoke(ctx)

	payload = ctx.respond_calls[0]
	embed = payload["embeds"][0]
	assert "Halo" in (embed.description or "")


@pytest.mark.asyncio
async def test_add_command_adds_favorite_and_returns_overview(favorites_group):
	group, shared = favorites_group
	add_cmd = group.subcommands["add"]
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])

	ctx = BaseCtx(guild_id=444, user_id=55)
	cmd_instance = object.__new__(add_cmd)
	cmd_instance.game = "valorant"

	bound_invoke = add_cmd.invoke.__get__(cmd_instance, add_cmd)
	await bound_invoke(ctx)

	favorites = shared.favorites_store.get_user_favorites(444, 55)
	assert favorites == ["valorant"]
	assert getattr(ctx, "_dropscout_deferred", False), "Expected mark_deferred to mark the context"
	payload = ctx.edited_initial or (ctx.respond_calls[-1] if ctx.respond_calls else None)
	assert payload, "Expected add command to send a payload"
	assert "Added **Valorant**" in payload.get("content", "")
	components = payload.get("components") or []
	assert components, "Expected updated overview components"


@pytest.mark.asyncio
async def test_add_command_uses_author_fallback(favorites_group):
	group, shared = favorites_group
	add_cmd = group.subcommands["add"]
	shared.game_catalog.merge_games([GameEntry(key="halo", name="Halo", weight=100)])

	ctx = AuthorCtx(guild_id=88, author_id=77)
	cmd_instance = object.__new__(add_cmd)
	cmd_instance.game = "halo"

	bound_invoke = add_cmd.invoke.__get__(cmd_instance, add_cmd)
	await bound_invoke(ctx)

	assert shared.favorites_store.get_user_favorites(88, 77) == ["halo"]
	payload = ctx.edited_initial or ctx.respond_calls[-1]
	assert payload["content"] == "Added **Halo** to your favorites."


@pytest.mark.asyncio
async def test_add_command_rejects_unknown_game(favorites_group):
	group, shared = favorites_group
	add_cmd = group.subcommands["add"]

	ctx = BaseCtx(guild_id=123, user_id=7)
	cmd_instance = object.__new__(add_cmd)
	cmd_instance.game = "unknown-game"

	bound_invoke = add_cmd.invoke.__get__(cmd_instance, add_cmd)
	await bound_invoke(ctx)

	payload = ctx.edited_initial or (ctx.respond_calls[-1] if ctx.respond_calls else None)
	assert payload, "Expected response prompting valid selection"
	assert payload.get("content") == "Select a game from the autocomplete suggestions to add it."
	assert not shared.favorites_store.get_user_favorites(123, 7)


@pytest.mark.asyncio
async def test_remove_command_removes_existing_favorite(favorites_group):
	group, shared = favorites_group
	remove_cmd = group.subcommands["remove"]
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])
	shared.favorites_store.add_favorite(999, 1, "valorant")

	ctx = BaseCtx(guild_id=999, user_id=1)
	cmd_instance = object.__new__(remove_cmd)
	cmd_instance.game = "valorant"

	bound_invoke = remove_cmd.invoke.__get__(cmd_instance, remove_cmd)
	await bound_invoke(ctx)

	assert shared.favorites_store.get_user_favorites(999, 1) == []
	payload = ctx.edited_initial or (ctx.respond_calls[-1] if ctx.respond_calls else None)
	assert payload, "Expected remove command to respond"
	assert payload.get("content") == "Removed **Valorant** from your favorites."
	assert getattr(ctx, "_dropscout_deferred", False), "Expected mark_deferred flag for remove"


@pytest.mark.asyncio
async def test_remove_command_handles_missing_favorite(favorites_group):
	group, shared = favorites_group
	remove_cmd = group.subcommands["remove"]
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])

	ctx = BaseCtx(guild_id=111, user_id=222)
	cmd_instance = object.__new__(remove_cmd)
	cmd_instance.game = "valorant"

	bound_invoke = remove_cmd.invoke.__get__(cmd_instance, remove_cmd)
	await bound_invoke(ctx)

	payload = ctx.edited_initial or (ctx.respond_calls[-1] if ctx.respond_calls else None)
	assert payload, "Expected remove command to respond even when not found"
	assert payload.get("content") == "**Valorant** is not currently in your favorites."


@pytest.mark.asyncio
async def test_remove_command_uses_member_fallback(favorites_group):
	group, shared = favorites_group
	remove_cmd = group.subcommands["remove"]
	shared.game_catalog.merge_games([GameEntry(key="halo", name="Halo", weight=100)])
	shared.favorites_store.add_favorite(77, 66, "halo")

	ctx = MemberCtx(guild_id=77, member_id=66)
	cmd_instance = object.__new__(remove_cmd)
	cmd_instance.game = "halo"

	bound_invoke = remove_cmd.invoke.__get__(cmd_instance, remove_cmd)
	await bound_invoke(ctx)

	assert shared.favorites_store.get_user_favorites(77, 66) == []


@pytest.mark.asyncio
async def test_view_command_requires_guild(favorites_group):
	group, _ = favorites_group
	view_cmd = group.subcommands["view"]
	ctx = BaseCtx(guild_id=None, user_id=42)
	bound_invoke = view_cmd.invoke.__get__(object.__new__(view_cmd), view_cmd)

	await bound_invoke(ctx)

	assert ctx.respond_calls, "Expected response when guild missing"
	payload = ctx.respond_calls[0]
	assert payload["content"] == "Favorites can only be managed inside a server."
	assert payload.get("ephemeral") is True


@pytest.mark.asyncio
async def test_view_command_handles_unknown_favorite_name(favorites_group):
	group, shared = favorites_group
	view_cmd = group.subcommands["view"]
	shared.favorites_store.add_favorite(7, 8, "unknown-game")
	ctx = BaseCtx(guild_id=7, user_id=8)
	bound_invoke = view_cmd.invoke.__get__(object.__new__(view_cmd), view_cmd)

	await bound_invoke(ctx)

	payload = ctx.respond_calls[0]
	embed = payload["embeds"][0]
	assert "unknown-game" in (embed.description or "")


@pytest.mark.asyncio
async def test_view_command_handles_rest_failure(favorites_group):
	group, shared = favorites_group
	view_cmd = group.subcommands["view"]
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])
	shared.favorites_store.add_favorite(1, 2, "valorant")
	app = StubApp(rest=StubRest(fail=True))
	ctx = BaseCtx(guild_id=1, user_id=2, app=app)
	bound_invoke = view_cmd.invoke.__get__(object.__new__(view_cmd), view_cmd)

	await bound_invoke(ctx)

	payload = ctx.respond_calls[0]
	assert payload.get("components") == []


@pytest.mark.asyncio
async def test_add_command_requires_guild(favorites_group):
	group, shared = favorites_group
	add_cmd = group.subcommands["add"]
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])
	ctx = BaseCtx(guild_id=None, user_id=1)
	cmd_instance = object.__new__(add_cmd)
	cmd_instance.game = "valorant"

	bound_invoke = add_cmd.invoke.__get__(cmd_instance, add_cmd)
	await bound_invoke(ctx)

	payload = ctx.respond_calls[0]
	assert payload["content"] == "Favorites can only be managed inside a server."


@pytest.mark.asyncio
async def test_add_command_blank_option_prompts_selection(favorites_group):
	group, shared = favorites_group
	add_cmd = group.subcommands["add"]
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])

	ctx = BaseCtx(guild_id=9, user_id=9)
	cmd_instance = object.__new__(add_cmd)
	cmd_instance.game = ""

	bound_invoke = add_cmd.invoke.__get__(cmd_instance, add_cmd)
	await bound_invoke(ctx)

	payload = ctx.edited_initial or ctx.respond_calls[-1]
	assert payload["content"] == "Select a game from the suggestions to add it."


@pytest.mark.asyncio
async def test_add_command_duplicate_returns_exists_message(favorites_group):
	group, shared = favorites_group
	add_cmd = group.subcommands["add"]
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])
	shared.favorites_store.add_favorite(10, 20, "valorant")

	ctx = BaseCtx(guild_id=10, user_id=20)
	cmd_instance = object.__new__(add_cmd)
	cmd_instance.game = "valorant"

	bound_invoke = add_cmd.invoke.__get__(cmd_instance, add_cmd)
	await bound_invoke(ctx)

	payload = ctx.edited_initial or ctx.respond_calls[-1]
	assert payload["content"] == "**Valorant** is already in your favorites."


@pytest.mark.asyncio
async def test_add_command_invalid_user_object(favorites_group):
	group, shared = favorites_group
	add_cmd = group.subcommands["add"]
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])

	ctx = BaseCtx(guild_id=15, user_id=99)
	ctx.user = object()
	cmd_instance = object.__new__(add_cmd)
	cmd_instance.game = "valorant"

	bound_invoke = add_cmd.invoke.__get__(cmd_instance, add_cmd)
	await bound_invoke(ctx)

	payload = ctx.respond_calls[0]
	assert payload["content"] == "Could not resolve your user information."


class FailDeferCtx(BaseCtx):
	async def defer(self, *args, **kwargs):
		raise RuntimeError("cannot defer")


@pytest.mark.asyncio
async def test_add_command_defer_failure_falls_back(favorites_group):
	group, shared = favorites_group
	add_cmd = group.subcommands["add"]
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])
	shared.favorites_store.add_favorite(5, 6, "valorant")

	ctx = FailDeferCtx(guild_id=5, user_id=6)
	cmd_instance = object.__new__(add_cmd)
	cmd_instance.game = "valorant"

	bound_invoke = add_cmd.invoke.__get__(cmd_instance, add_cmd)
	await bound_invoke(ctx)

	payload = ctx.respond_calls[-1]
	assert payload["content"] == "**Valorant** is already in your favorites."
	assert not getattr(ctx, "_dropscout_deferred", False)


@pytest.mark.asyncio
async def test_remove_command_requires_guild(favorites_group):
	group, shared = favorites_group
	remove_cmd = group.subcommands["remove"]
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])

	ctx = BaseCtx(guild_id=None, user_id=1)
	cmd_instance = object.__new__(remove_cmd)
	cmd_instance.game = "valorant"

	bound_invoke = remove_cmd.invoke.__get__(cmd_instance, remove_cmd)
	await bound_invoke(ctx)

	payload = ctx.respond_calls[0]
	assert payload["content"] == "Favorites can only be managed inside a server."


@pytest.mark.asyncio
async def test_remove_command_blank_option_prompts_selection(favorites_group):
	group, shared = favorites_group
	remove_cmd = group.subcommands["remove"]
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])
	shared.favorites_store.add_favorite(4, 4, "valorant")

	ctx = BaseCtx(guild_id=4, user_id=4)
	cmd_instance = object.__new__(remove_cmd)
	cmd_instance.game = " "

	bound_invoke = remove_cmd.invoke.__get__(cmd_instance, remove_cmd)
	await bound_invoke(ctx)

	payload = ctx.edited_initial or ctx.respond_calls[-1]
	assert payload["content"] == "Select a favorite game to remove."


@pytest.mark.asyncio
async def test_remove_command_unknown_game_uses_key(favorites_group):
	group, shared = favorites_group
	remove_cmd = group.subcommands["remove"]
	ctx = BaseCtx(guild_id=12, user_id=12)
	cmd_instance = object.__new__(remove_cmd)
	cmd_instance.game = "mystery"

	bound_invoke = remove_cmd.invoke.__get__(cmd_instance, remove_cmd)
	await bound_invoke(ctx)

	payload = ctx.edited_initial or ctx.respond_calls[-1]
	assert payload["content"] == "**mystery** is not currently in your favorites."


@pytest.mark.asyncio
async def test_remove_command_invalid_user_object(favorites_group):
	group, shared = favorites_group
	remove_cmd = group.subcommands["remove"]
	ctx = BaseCtx(guild_id=14, user_id=33)
	ctx.user = object()
	cmd_instance = object.__new__(remove_cmd)
	cmd_instance.game = "valorant"

	bound_invoke = remove_cmd.invoke.__get__(cmd_instance, remove_cmd)
	await bound_invoke(ctx)

	payload = ctx.respond_calls[0]
	assert payload["content"] == "Could not resolve your user information."


@pytest.mark.asyncio
async def test_check_command_requires_guild(favorites_group):
	group, _ = favorites_group
	check_cmd = group.subcommands["check"]
	ctx = BaseCtx(guild_id=None, user_id=1)
	bound_invoke = check_cmd.invoke.__get__(object.__new__(check_cmd), check_cmd)

	await bound_invoke(ctx)

	assert ctx.respond_calls[0]["content"] == "Favorites can only be managed inside a server."


@pytest.mark.asyncio
async def test_check_command_requires_user(favorites_group):
	group, shared = favorites_group
	check_cmd = group.subcommands["check"]
	ctx = BaseCtx(guild_id=1, user_id=None)
	bound_invoke = check_cmd.invoke.__get__(object.__new__(check_cmd), check_cmd)

	await bound_invoke(ctx)

	assert ctx.respond_calls[0]["content"] == "Could not resolve your user information."


class FinalizerRecorder:
	def __init__(self) -> None:
		self.messages: list[str | None] = []

	async def __call__(self, ctx, *, message=None):
		self.messages.append(message)


@pytest.mark.asyncio
async def test_check_command_no_favorites_calls_finalize(monkeypatch, favorites_group):
	group, shared = favorites_group
	check_cmd = group.subcommands["check"]
	recorder = FinalizerRecorder()
	monkeypatch.setattr(shared, "finalize_interaction", recorder)

	ctx = BaseCtx(guild_id=5, user_id=5)
	bound_invoke = check_cmd.invoke.__get__(object.__new__(check_cmd), check_cmd)

	await bound_invoke(ctx)

	assert recorder.messages == ["You have no favorite games yet."]


@pytest.mark.asyncio
async def test_check_command_fetch_failure(monkeypatch, favorites_group):
	group, shared = favorites_group
	check_cmd = group.subcommands["check"]
	shared.favorites_store.add_favorite(1, 1, "valorant")
	recorder = FinalizerRecorder()
	monkeypatch.setattr(shared, "finalize_interaction", recorder)

	async def boom():
		raise RuntimeError("boom")

	monkeypatch.setattr(shared, "get_campaigns_cached", boom)

	ctx = BaseCtx(guild_id=1, user_id=1)
	bound_invoke = check_cmd.invoke.__get__(object.__new__(check_cmd), check_cmd)

	await bound_invoke(ctx)

	assert recorder.messages == ["Failed to load campaigns."]


@pytest.mark.asyncio
async def test_check_command_no_active_campaigns(monkeypatch, favorites_group):
	group, shared = favorites_group
	check_cmd = group.subcommands["check"]
	shared.favorites_store.add_favorite(2, 3, "valorant")

	async def fake_campaigns():
		return [
			CampaignRecord(
				id="camp-1",
				name="Valorant Drops",
				status="EXPIRED",
				game_name="Valorant",
				game_slug="valorant",
				game_box_art=None,
				starts_at=None,
				ends_at=None,
				benefits=[],
			)
		]

	recorder = FinalizerRecorder()
	monkeypatch.setattr(shared, "finalize_interaction", recorder)
	monkeypatch.setattr(shared, "get_campaigns_cached", fake_campaigns)

	ctx = BaseCtx(guild_id=2, user_id=3)
	bound_invoke = check_cmd.invoke.__get__(object.__new__(check_cmd), check_cmd)

	await bound_invoke(ctx)

	assert recorder.messages == ["No active campaigns for your favorites right now."]


@pytest.mark.asyncio
async def test_check_command_multiple_campaigns_show_footer(monkeypatch, favorites_group):
	group, shared = favorites_group
	check_cmd = group.subcommands["check"]
	shared.favorites_store.add_favorite(3, 4, "valorant")
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])

	campaigns = [
		CampaignRecord(
			id="camp-1",
			name="Valorant Drops 1",
			status="ACTIVE",
			game_name="Valorant",
			game_slug="valorant",
			game_box_art=None,
			starts_at=None,
			ends_at="2025-01-01T00:00:00+00:00",
			benefits=[BenefitRecord(id="b1", name="Reward 1", image_url=None)],
		),
		CampaignRecord(
			id="camp-2",
			name="Valorant Drops 2",
			status="ACTIVE",
			game_name="Valorant",
			game_slug="valorant",
			game_box_art=None,
			starts_at=None,
			ends_at="2025-01-02T00:00:00+00:00",
			benefits=[BenefitRecord(id="b2", name="Reward 2", image_url=None)],
		),
	]

	async def fake_campaigns():
		return campaigns

	monkeypatch.setattr(shared, "get_campaigns_cached", fake_campaigns)

	ctx = BaseCtx(guild_id=3, user_id=4)
	bound_invoke = check_cmd.invoke.__get__(object.__new__(check_cmd), check_cmd)

	await bound_invoke(ctx)

	payload = ctx.edited_initial or ctx.respond_calls[-1]
	assert "Valorant" in payload["content"]
	embed = payload["embeds"][0]
	assert embed.footer and "campaign 1 of 2" in embed.footer.text  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_check_command_marks_deferred_flag(monkeypatch, favorites_group):
	group, shared = favorites_group
	check_cmd = group.subcommands["check"]
	shared.game_catalog.merge_games([GameEntry(key="halo", name="Halo", weight=100)])
	shared.favorites_store.add_favorite(9, 9, "halo")

	async def fake_campaigns():
		return [
			CampaignRecord(
				id="camp-1",
				name="Halo Campaign",
				status="ACTIVE",
				game_name="Halo",
				game_slug="halo",
				game_box_art=None,
				starts_at=None,
				ends_at=None,
				benefits=[BenefitRecord(id="b1", name="Reward", image_url=None)],
			)
		]

	monkeypatch.setattr(shared, "get_campaigns_cached", fake_campaigns)

	ctx = BaseCtx(guild_id=9, user_id=9)
	bound_invoke = check_cmd.invoke.__get__(object.__new__(check_cmd), check_cmd)
	await bound_invoke(ctx)

	assert getattr(ctx, "_dropscout_deferred", False), "Expected check command to mark deferred contexts"


@pytest.mark.asyncio
async def test_check_command_uses_member_fallback(monkeypatch, favorites_group):
	group, shared = favorites_group
	check_cmd = group.subcommands["check"]
	shared.game_catalog.merge_games([GameEntry(key="halo", name="Halo", weight=100)])
	shared.favorites_store.add_favorite(11, 22, "halo")

	async def fake_campaigns():
		return [
			CampaignRecord(
				id="camp-1",
				name="Halo Campaign",
				status="ACTIVE",
				game_name="Halo",
				game_slug="halo",
				game_box_art=None,
				starts_at=None,
				ends_at=None,
				benefits=[BenefitRecord(id="b1", name="Reward", image_url=None)],
			)
		]

	monkeypatch.setattr(shared, "get_campaigns_cached", fake_campaigns)

	ctx = MemberCtx(guild_id=11, member_id=22)
	bound_invoke = check_cmd.invoke.__get__(object.__new__(check_cmd), check_cmd)
	await bound_invoke(ctx)

	payload = ctx.edited_initial or ctx.respond_calls[-1]
	assert "Halo" in payload["content"]


@pytest.mark.asyncio
async def test_check_command_uses_author_fallback(monkeypatch, favorites_group):
	group, shared = favorites_group
	check_cmd = group.subcommands["check"]
	shared.game_catalog.merge_games([GameEntry(key="halo", name="Halo", weight=100)])
	shared.favorites_store.add_favorite(13, 14, "halo")

	async def fake_campaigns():
		return [
			CampaignRecord(
				id="camp-1",
				name="Halo Campaign",
				status="ACTIVE",
				game_name="Halo",
				game_slug="halo",
				game_box_art=None,
				starts_at=None,
				ends_at=None,
				benefits=[BenefitRecord(id="b1", name="Reward", image_url=None)],
			)
		]

	monkeypatch.setattr(shared, "get_campaigns_cached", fake_campaigns)

	ctx = AuthorCtx(guild_id=13, author_id=14)
	bound_invoke = check_cmd.invoke.__get__(object.__new__(check_cmd), check_cmd)
	await bound_invoke(ctx)

	payload = ctx.edited_initial or ctx.respond_calls[-1]
	assert "Halo" in payload["content"]


class SendCtx:
	def __init__(self, *, edit_exception: Exception | None = None) -> None:
		self.edit_exception = edit_exception
		self.edit_calls: list[dict[str, object]] = []
		self.respond_calls: list[dict[str, object]] = []

	async def edit_initial_response(self, **kwargs):
		if self.edit_exception:
			raise self.edit_exception
		self.edit_calls.append(kwargs)

	async def respond(self, **kwargs):
		self.respond_calls.append(kwargs)


@pytest.mark.asyncio
async def test_send_ephemeral_response_edits_when_deferred():
	ctx = SendCtx()
	await favorites_mod._send_ephemeral_response(ctx, True, content="hello")
	assert ctx.edit_calls == [{"content": "hello"}]
	assert ctx.respond_calls == []


@pytest.mark.asyncio
async def test_send_ephemeral_response_falls_back_to_respond_on_error():
	ctx = SendCtx(edit_exception=RuntimeError("fail"))
	await favorites_mod._send_ephemeral_response(ctx, True, content="hello")
	assert ctx.respond_calls == [{"content": "hello", "flags": hikari.MessageFlag.EPHEMERAL}]


@pytest.mark.asyncio
async def test_send_ephemeral_response_non_deferred_sets_ephemeral_flag():
	ctx = SendCtx()
	await favorites_mod._send_ephemeral_response(ctx, False, content="hello")
	assert ctx.respond_calls == [{"content": "hello", "flags": hikari.MessageFlag.EPHEMERAL}]


def test_build_overview_no_favorites_text(shared):
	app = StubApp()
	embed, components = favorites_mod._build_overview(app, shared, guild_id=1, user_id=1)
	assert embed.description == "You have no favorite games yet."
	assert components
	row_payload, _ = components[0].build()
	assert row_payload["components"][0]["custom_id"] == favorites_mod.REFRESH_BUTTON_ID


def test_build_overview_handles_rest_failure(shared):
	app = StubApp(rest=StubRest(fail=True))
	shared.favorites_store.add_favorite(1, 2, "valorant")
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])
	embed, components = favorites_mod._build_overview(app, shared, guild_id=1, user_id=2)
	assert "Valorant" in (embed.description or "")
	assert components == []


def test_build_overview_truncates_select_menu(shared):
	app = StubApp()
	entries = [GameEntry(key=f"game-{i}", name=f"Game {i}", weight=1) for i in range(30)]
	shared.game_catalog.merge_games(entries)
	for idx, entry in enumerate(entries, start=1):
		shared.favorites_store.add_favorite(5, 5, entry.key)
	embed, components = favorites_mod._build_overview(app, shared, guild_id=5, user_id=5)
	assert "Game 1" in (embed.description or "")
	assert len(components) == 2
	select_row = components[1]
	menu = next(comp for comp in select_row.components if isinstance(comp, StubSelectMenuBuilder))
	assert menu.max_values == 25
	assert len(menu.options) == 25


def test_build_favorite_pages_filters_and_orders(shared):
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])
	favorites = ["valorant", "unknown"]
	campaigns = [
		CampaignRecord(
			id="camp-old",
			name="Old",
			status="ACTIVE",
			game_name="Valorant",
			game_slug="valorant",
			game_box_art=None,
			starts_at=None,
			ends_at="2025-01-02T00:00:00+00:00",
			benefits=[],
		),
		CampaignRecord(
			id="camp-new",
			name="New",
			status="ACTIVE",
			game_name="Valorant",
			game_slug="valorant",
			game_box_art=None,
			starts_at=None,
			ends_at="2025-01-01T00:00:00+00:00",
			benefits=[],
		),
		CampaignRecord(
			id="camp-expired",
			name="Expired",
			status="EXPIRED",
			game_name="Valorant",
			game_slug="valorant",
			game_box_art=None,
			starts_at=None,
			ends_at=None,
			benefits=[],
		),
	]
	pages = favorites_mod._build_favorite_pages(shared, favorites, campaigns)
	assert [campaign.id for _, campaign, _, _ in pages] == ["camp-new", "camp-old"]
	assert pages[0][2:] == (1, 2)
	assert pages[1][2:] == (2, 2)


def test_build_check_page_payload_clamps_index_and_buttons(shared):
	shared.game_catalog.merge_games([GameEntry(key="valorant", name="Valorant", weight=100)])
	favorites = ["valorant"]
	campaign = CampaignRecord(
		id="camp-1",
		name="Drops",
		status="ACTIVE",
		game_name="Valorant",
		game_slug="valorant",
		game_box_art=None,
		starts_at=None,
		ends_at="2025-01-01T00:00:00+00:00",
		benefits=[BenefitRecord(id="b1", name="Reward", image_url=None)],
	)
	pages = favorites_mod._build_favorite_pages(shared, favorites, [campaign])
	content, embeds, components = favorites_mod._build_check_page_payload(StubApp(), 42, pages, 10)
	assert "[1/1]" in content
	assert embeds and embeds[0].title == "Valorant"
	assert not components


def test_build_check_page_payload_multiple_pages(shared):
	shared.game_catalog.merge_games(
		[
			GameEntry(key="valorant", name="Valorant", weight=100),
		]
	)
	favorites = ["valorant"]
	campaigns = [
		CampaignRecord(
			id="camp-1",
			name="One",
			status="ACTIVE",
			game_name="Valorant",
			game_slug="valorant",
			game_box_art=None,
			starts_at=None,
			ends_at="2025-01-01T00:00:00+00:00",
			benefits=[BenefitRecord(id="b1", name="One", image_url=None)],
		),
		CampaignRecord(
			id="camp-2",
			name="Two",
			status="ACTIVE",
			game_name="Valorant",
			game_slug="valorant",
			game_box_art=None,
			starts_at=None,
			ends_at="2025-01-02T00:00:00+00:00",
			benefits=[BenefitRecord(id="b2", name="Two", image_url=None)],
		),
	]
	pages = favorites_mod._build_favorite_pages(shared, favorites, campaigns)
	content, embeds, components = favorites_mod._build_check_page_payload(StubApp(), 42, pages, 0)
	assert components
	row_payload, _ = components[0].build()
	assert row_payload["components"][0]["disabled"] is True
	assert row_payload["components"][1]["disabled"] is False
