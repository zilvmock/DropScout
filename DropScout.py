"""DropScout — Discord bot entrypoint.

Sets up the Hikari + Lightbulb client, registers commands, and starts the
background Twitch Drops monitor. Configuration is provided via environment
variables loaded from .env when present.
"""

import os
import asyncio
import hikari
import lightbulb
from dotenv import load_dotenv

# Optional: use uvloop on UNIX-like systems for better event loop performance
if os.name != "nt":
    try:
        import uvloop  # type: ignore

        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except Exception:
        # If uvloop isn't available, continue with default asyncio loop
        pass

from functionality.twitch_drops import DropsMonitor, GuildConfigStore
from functionality.twitch_drops.commands import register_commands

# Load .env file and read token
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
	raise RuntimeError("DISCORD_TOKEN is not set in the environment or .env file")

# Optional: fast command registration to a specific guild during development
# Provide a comma-separated list of guild IDs via GUILD_IDS in .env (e.g. GUILD_IDS=123,456)
# Guild IDs: Unique numbers for your servers
# Why: Guild-scoped commands appear almost instantly; global commands can take up to an hour.
raw_guilds = os.getenv("GUILD_IDS", "").strip()
default_enabled_guilds: list[int] = []
if raw_guilds:
	for part in raw_guilds.split(","):
		part = part.strip()
		if part:
			try:
				default_enabled_guilds.append(int(part))
			except ValueError:
				raise RuntimeError(f"Invalid guild id in GUILD_IDS: {part!r}")

# Create the Hikari gateway bot
bot = hikari.GatewayBot(
	token=TOKEN,
	intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
)

# Create the Lightbulb client from the Hikari app (Lightbulb v3 style)
client = lightbulb.client_from_app(
	bot,
	default_enabled_guilds=tuple(default_enabled_guilds),
)

# Start/stop Lightbulb with the Hikari app lifecycle
bot.subscribe(hikari.StartedEvent, client.start)
bot.subscribe(hikari.StoppingEvent, client.stop)


@bot.listen(hikari.StartedEvent)
async def on_started(_: hikari.StartedEvent) -> None:
	"""Log a ready message once the gateway session is established."""
	print("✅ Bot is online!")


REFRESH_MINUTES = int(os.getenv("TWITCH_REFRESH_MINUTES", "30") or 30)
GUILD_STORE_PATH = os.getenv("TWITCH_GUILD_STORE_PATH", "data/guild_config.json")

_monitor: DropsMonitor | None = None
_guild_store = GuildConfigStore(GUILD_STORE_PATH)

# Register commands (kept separate for maintainability)
register_commands(client)


@bot.listen(hikari.StartedEvent)
async def _note_started(_: hikari.StartedEvent) -> None:
	"""Start the background monitor after the app has started."""
	global _monitor
	# Start the periodic monitor
	_monitor = DropsMonitor(
		bot,
		interval_minutes=REFRESH_MINUTES,
		state_path=os.getenv("TWITCH_STATE_PATH", "data/campaigns_state.json"),
		guild_store_path=GUILD_STORE_PATH,
		notify_on_boot=(os.getenv("TWITCH_NOTIFY_ON_BOOT", "false").lower() == "true"),
	)
	_monitor.start()
	print("DropScout bot ready. Monitoring for campaign changes...")


@bot.listen(hikari.StoppingEvent)
async def _note_stopping(_: hikari.StoppingEvent) -> None:
	"""Stop the background monitor when the app is shutting down."""
	global _monitor
	if _monitor:
		await _monitor.stop()


@bot.listen(hikari.GuildJoinEvent)
async def _on_guild_join(event: hikari.GuildJoinEvent) -> None:
	"""Default the notifications channel to the guild's system channel on join.

	If a server invites the bot and no notifications channel has been configured
	yet, this attempts to use the system channel as a sensible default.
	"""
	# Set a reasonable default notification channel on join if none configured
	gid = int(event.guild_id)
	if _guild_store.get_channel_id(gid) is None:
		try:
			g = await bot.rest.fetch_guild(gid)
			scid = getattr(g, "system_channel_id", None)
			if scid:
				_guild_store.set_channel_id(gid, int(scid))
		except Exception:
			pass


# Run the bot
if __name__ == "__main__":
	bot.run()
