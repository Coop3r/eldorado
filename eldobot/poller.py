"""Polls Eldorado for new orders, order state changes and notifications.

The Seller API has no webhooks, so this is the only way to get alerts.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import discord

from .eldorado import EldoradoClient, EldoradoError
from .formatting import (
    boosting_request_embed,
    humanize,
    notification_embed,
    notification_event,
    notification_id,
    order_embed,
)
from .store import Store

log = logging.getLogger(__name__)

POLL_SECONDS = 60
ORDERS = "order"
NOTIFICATIONS = "notification"
NEW_ORDER_STATES = {"Initialized", "PendingReview", "Paid"}
# Every alert pings the owner except these notification events.
QUIET_EVENTS = {"BoostingRequestCreated"}
# Only the newest unread notifications are fetched, so old ones can resurface
# once newer ones are read on the site. Anything older than this is not announced.
NOTIFICATION_MAX_AGE = timedelta(hours=24)

# send(embed, ping_owner)
Sender = Callable[[discord.Embed, bool], Awaitable[None]]


class Poller:
    def __init__(self, eldorado: EldoradoClient, store: Store, send: Sender):
        self._eldorado = eldorado
        self._store = store
        self._send = send

    async def tick(self) -> None:
        for check in (self._check_orders, self._check_notifications):
            try:
                await check()
            except Exception:
                # Keep polling; a failed check is retried on the next tick.
                log.exception("%s failed", check.__name__)

    async def _check_orders(self) -> None:
        orders = await self._eldorado.orders(page_size=30)
        seeded = self._store.is_seeded(ORDERS)
        # Oldest first, so alerts arrive in the order things happened.
        for order in reversed(orders):
            state = (order.get("state") or {}).get("state", "")
            previous = self._store.get_state(ORDERS, order["id"])
            if previous == state:
                continue
            if seeded:
                is_new = previous is None and state in NEW_ORDER_STATES
                heading = "New order" if is_new else f"Order {humanize(state).lower()}"
                await self._send(order_embed(order, heading), True)
            self._store.set_state(ORDERS, order["id"], state)
        self._store.mark_seeded(ORDERS)

    async def _check_notifications(self) -> None:
        items = await self._eldorado.unread_notifications()
        seeded = self._store.is_seeded(NOTIFICATIONS)
        for item in reversed(items):
            id = notification_id(item)
            if id is None or self._store.get_state(NOTIFICATIONS, id) is not None:
                continue
            # Order events are already reported, with more detail, by _check_orders.
            event = notification_event(item)
            if seeded and _is_recent(item) and not event.startswith("Order"):
                await self._send(await self._embed_for(item, event), event not in QUIET_EVENTS)
            self._store.set_state(NOTIFICATIONS, id)
        self._store.mark_seeded(NOTIFICATIONS)

    async def _embed_for(self, item: dict, event: str) -> discord.Embed:
        request_id = ((item.get("notification") or {}).get("details") or {}).get("detailsId")
        if event == "BoostingRequestCreated" and request_id:
            try:
                return boosting_request_embed(await self._eldorado.boosting_request(request_id))
            except EldoradoError as exc:
                log.warning("Could not load boosting request %s: %s", request_id, exc)
        return notification_embed(item)


def _is_recent(item: dict) -> bool:
    inner = item.get("notification") or item.get("customNotification") or {}
    try:
        sent_at = datetime.fromisoformat(inner.get("notificationDate") or "")
    except ValueError:
        return True
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - sent_at <= NOTIFICATION_MAX_AGE
