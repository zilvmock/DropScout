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
