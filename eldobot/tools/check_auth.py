"""Verify the Eldorado credentials in .env: python -m eldobot.tools.check_auth

Read-only. It exchanges the credential for an access token and changes nothing.
"""

import asyncio
import sys

from ..config import ConfigError, load_settings
from ..eldorado import EldoradoClient, EldoradoError


async def main() -> int:
    try:
        settings = load_settings(require_discord=False)
    except ConfigError as exc:
        print(exc)
        return 1
    client = EldoradoClient(
        settings.eldorado_client_id, settings.eldorado_client_secret, settings.eldorado_base_url
    )
    try:
        await client.access_token()
    except EldoradoError as exc:
        print(f"Eldorado rejected the credential: {exc}")
        return 1
    finally:
        await client.aclose()
    print(f"OK: got an access token, valid for {client.token_seconds_left():.0f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
