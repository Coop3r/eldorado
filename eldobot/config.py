import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    eldorado_client_id: str
    eldorado_client_secret: str
    eldorado_base_url: str
    discord_token: str | None
    discord_guild_id: int | None
    discord_owner_id: int | None
    discord_notify_channel_id: int | None


def _int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a numeric Discord ID, got {raw!r}") from None


def load_settings(*, require_discord: bool = True) -> Settings:
    load_dotenv()
    settings = Settings(
        eldorado_client_id=os.getenv("ELDORADO_CLIENT_ID", "").strip(),
        eldorado_client_secret=os.getenv("ELDORADO_CLIENT_SECRET", "").strip(),
        eldorado_base_url=os.getenv("ELDORADO_BASE_URL", "").strip() or "https://www.eldorado.gg",
        discord_token=os.getenv("DISCORD_TOKEN", "").strip() or None,
        discord_guild_id=_int("DISCORD_GUILD_ID"),
        discord_owner_id=_int("DISCORD_OWNER_ID"),
        discord_notify_channel_id=_int("DISCORD_NOTIFY_CHANNEL_ID"),
    )

    missing = []
    if not settings.eldorado_client_id:
        missing.append("ELDORADO_CLIENT_ID")
    if not settings.eldorado_client_secret:
        missing.append("ELDORADO_CLIENT_SECRET")
    if require_discord:
        # The owner ID is mandatory: without it anyone in the server could run commands.
        for name, value in (
            ("DISCORD_TOKEN", settings.discord_token),
            ("DISCORD_GUILD_ID", settings.discord_guild_id),
            ("DISCORD_OWNER_ID", settings.discord_owner_id),
        ):
            if not value:
                missing.append(name)
    if missing:
        raise ConfigError(f"Missing in .env: {', '.join(missing)} (see .env.example)")
    return settings
