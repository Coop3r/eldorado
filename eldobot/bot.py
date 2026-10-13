import logging

import discord
from discord import app_commands
from discord.ext import tasks

from .commands import register_commands
from .config import Settings
from .eldorado import EldoradoClient, EldoradoError
from .poller import POLL_SECONDS, Poller
from .store import Store

log = logging.getLogger(__name__)


class OwnerOnlyTree(app_commands.CommandTree):
    """Rejects every command and autocomplete that does not come from the configured owner."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == interaction.client.settings.discord_owner_id:
            return True
        if interaction.type is discord.InteractionType.autocomplete:
            await interaction.response.autocomplete([])
        else:
            await interaction.response.send_message("This bot is private.", ephemeral=True)
        return False

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        cause = getattr(error, "original", error)
        if isinstance(cause, EldoradoError):
            text = f"Eldorado returned an error: {cause}"
        else:
            log.exception("Command failed", exc_info=cause)
            text = f"Command failed: {type(cause).__name__}"
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class EldoBot(discord.Client):
    def __init__(self, settings: Settings):
        super().__init__(intents=discord.Intents.default())
        self.settings = settings
        self.eldorado = EldoradoClient(
            settings.eldorado_client_id,
            settings.eldorado_client_secret,
            settings.eldorado_base_url,
        )
        self.store = Store()
        self.poller = Poller(self.eldorado, self.store, self.notify)
        self.tree = OwnerOnlyTree(self)
        register_commands(self.tree)

    async def setup_hook(self) -> None:
        # Guild-scoped commands show up immediately; global ones can take an hour.
        guild = discord.Object(id=self.settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.poll.start()

    async def on_ready(self) -> None:
        log.info("Logged in to Discord as %s", self.user)
        channel_id = self.settings.discord_notify_channel_id
        if not channel_id:
            log.warning("DISCORD_NOTIFY_CHANNEL_ID is not set; alerts go to the owner's DMs")
            return
        channel = self.get_channel(channel_id)
        if channel is None:
            log.warning("Notify channel %s is not visible to the bot; alerts will fail", channel_id)
        elif not channel.permissions_for(channel.guild.me).send_messages:
            log.warning("The bot cannot send messages in #%s; alerts will fail", channel.name)
        else:
            log.info("Alerts will be posted in #%s", channel.name)

    async def close(self) -> None:
        self.poll.cancel()
        await self.eldorado.aclose()
        self.store.close()
        await super().close()

    @tasks.loop(seconds=POLL_SECONDS)
    async def poll(self) -> None:
        await self.poller.tick()

    @poll.before_loop
    async def before_poll(self) -> None:
        await self.wait_until_ready()

    async def notify(self, embed: discord.Embed, ping_owner: bool) -> None:
        """Post an alert to the notify channel, or DM the owner if none is configured."""
        owner_id = self.settings.discord_owner_id
        channel_id = self.settings.discord_notify_channel_id
        if channel_id:
            target = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        else:
            target = self.get_user(owner_id) or await self.fetch_user(owner_id)
        content = f"<@{owner_id}>" if ping_owner and channel_id else None
        await target.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=owner_id)]),
        )
