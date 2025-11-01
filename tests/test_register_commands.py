import hikari
import lightbulb

from functionality.twitch_drops.commands import register_commands


def test_register_commands_adds_expected():
	# Build a test app + client (no network connection made)
	bot = hikari.GatewayBot(token="X", intents=hikari.Intents.ALL_UNPRIVILEGED)
	client = lightbulb.client_from_app(bot)
	names = set(register_commands(client))
	expected = {
		"hello",
		"help",
		"drops_active",
		"drops_this_week",
		"drops_set_channel",
		"drops_channel",
		"drops_search_game",
		"drops_favorites",
	}
	assert expected.issubset(names)
