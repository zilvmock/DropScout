import pytest

from functionality.twitch_drops.config import GuildConfigStore
from functionality.twitch_drops.differ import DropsDiff
from functionality.twitch_drops.models import BenefitRecord, CampaignRecord
from functionality.twitch_drops.favorites import FavoritesStore
from functionality.twitch_drops.game_catalog import GameCatalog, GameEntry
from functionality.twitch_drops.notifier import DropsNotifier


class StubRest:
	def __init__(self, guild_id: int, channel_id: int):
		self._guild_id = guild_id
		self._channel_id = channel_id
		self.sent: list[tuple[int, str | None, list, dict]] = []

	class _Guild:
		def __init__(self, guild_id: int):
			self.id = guild_id
			self.system_channel_id = None

	async def fetch_my_guilds(self):
		return [self._Guild(self._guild_id)]

	async def create_message(self, channel_id, *, content=None, embeds=None, **kwargs):
		self.sent.append((int(channel_id), content, list(embeds or []), dict(kwargs)))


class StubApp:
	def __init__(self, rest: StubRest):
		self.rest = rest


@pytest.mark.asyncio
async def test_notifier_posts_collage_and_mentions(monkeypatch, tmp_path):
	rest = StubRest(guild_id=123, channel_id=999)
	app = StubApp(rest)

	guild_store = GuildConfigStore(str(tmp_path / "guild.json"))
	guild_store.set_channel_id(123, 999)
	favorites = FavoritesStore(str(tmp_path / "favorites.json"))
	favorites.add_favorite(123, 111, "valorant")
	favorites.add_favorite(123, 222, "valorant")

	catalog = GameCatalog(str(tmp_path / "catalog.json"))
	catalog.merge_games(
		[
			GameEntry(
				key="valorant",
				name="Valorant",
				weight=500,
				aliases=["valorant"],
				sources=["seed"],
			)
		]
	)

	monkeypatch.setenv("DROPS_MAX_ATTACHMENTS_PER_NOTIFY", "1")
	monkeypatch.setenv("DROPS_SEND_DELAY_MS", "0")

	async def fake_collage(campaign, **kwargs):
		return b"png-bytes", "file.png"

	monkeypatch.setattr("functionality.twitch_drops.notifier.build_benefits_collage", fake_collage)

	async def no_sleep(*args, **kwargs):
		return None

	monkeypatch.setattr("functionality.twitch_drops.notifier.asyncio.sleep", no_sleep)

	notifier = DropsNotifier(app, guild_store, favorites, catalog)

	campaign = CampaignRecord(
		id="camp1",
		name="Valorant Drops",
		status="ACTIVE",
		game_name="Valorant",
		game_slug="valorant",
		game_box_art=None,
		starts_at=None,
		ends_at=None,
		benefits=[BenefitRecord(id="b1", name="Reward", image_url="https://img.example/1.png")],
	)
	diff = DropsDiff(activated=[campaign])

	await notifier.notify(diff)

	assert rest.sent, "notification should be sent"
	channel_id, content, embeds, kwargs = rest.sent[0]
	assert channel_id == 999
	assert "<@111>" in (content or "")
	assert "<@222>" in (content or "")
	assert kwargs.get("user_mentions") == [111, 222]
	assert embeds and embeds[0].title == "Valorant"
	assert embeds[0].image is not None


def test_join_mentions_truncates(tmp_path):
	rest = StubRest(guild_id=123, channel_id=999)
	app = StubApp(rest)
	guild_store = GuildConfigStore(str(tmp_path / "guild.json"))
	favorites = FavoritesStore(str(tmp_path / "favorites.json"))
	catalog = GameCatalog(str(tmp_path / "catalog.json"))
	notifier = DropsNotifier(app, guild_store, favorites, catalog)

	ids = [100 + i for i in range(10)]
	text, included = notifier._join_mentions(ids, limit=15)
	assert text.endswith("…")
	# Should include at least the first user mention and capture IDs for allowed mentions
	assert included and included[0] == 100
