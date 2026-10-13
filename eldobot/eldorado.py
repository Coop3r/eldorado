"""Client for the Eldorado Seller API.

Endpoints follow the Swagger reference linked from Dashboard > Seller API
(https://www.eldorado.gg/swagger/seller/index.html, needs a logged-in browser).
There is no sandbox: every non-GET call here acts on live offers and orders.
"""

import asyncio
import time
from typing import Any

import httpx

TOKEN_PATH = "/api/authentication/seller/token"
# Eldorado access tokens last 15 minutes.
DEFAULT_TOKEN_TTL = 15 * 60
# Refresh this long before expiry so a request never goes out with a dying token.
REFRESH_MARGIN = 60
MAX_RATE_LIMIT_RETRIES = 3
MAX_RETRY_AFTER = 30.0


class EldoradoError(Exception):
    def __init__(self, status: int, messages: list[str], correlation_id: str | None = None):
        self.status = status
        self.messages = messages
        self.correlation_id = correlation_id
        super().__init__(f"HTTP {status}: {'; '.join(messages) or 'no message'}")


def _raise_for_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    messages: list[str] = []
    correlation_id = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        messages = [str(m) for m in body.get("messages") or []]
        correlation_id = body.get("correlationId")
    if not messages and response.text:
        messages = [response.text[:300]]
    raise EldoradoError(response.status_code, messages, correlation_id)


def _parse_token(body: Any) -> tuple[str, float]:
    """Return (access_token, lifetime_seconds) from the token response."""
    if isinstance(body, str) and body:
        return body, DEFAULT_TOKEN_TTL
    if isinstance(body, dict):
        token = next(
            (body[k] for k in ("accessToken", "access_token", "token") if body.get(k)), None
        )
        if isinstance(token, str):
            ttl = body.get("expiresIn") or body.get("expires_in") or DEFAULT_TOKEN_TTL
            return token, float(ttl)
        keys = sorted(body)
    else:
        keys = type(body).__name__
    # Report the shape only, never the values, since they are secrets.
    raise EldoradoError(200, [f"Unrecognised token response shape: {keys}"])


class EldoradoClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = "https://www.eldorado.gg",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=30,
            transport=transport,
            headers={"User-Agent": "eldobot/0.1", "Accept": "application/json"},
        )
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def access_token(self, *, force_refresh: bool = False) -> str:
        async with self._token_lock:
            fresh = time.monotonic() < self._token_expires_at - REFRESH_MARGIN
            if self._token and fresh and not force_refresh:
                return self._token
            response = await self._http.post(
                TOKEN_PATH,
                json={"clientId": self._client_id, "clientSecret": self._client_secret},
            )
            _raise_for_error(response)
            self._token, ttl = _parse_token(response.json())
            self._token_expires_at = time.monotonic() + ttl
            return self._token

    def token_seconds_left(self) -> float:
        return max(0.0, self._token_expires_at - time.monotonic())

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Send an authenticated request and return the decoded JSON body, if any."""
        reauthenticated = False
        rate_limit_retries = 0
        while True:
            token = await self.access_token()
            response = await self._http.request(
                method,
                path,
                params=params,
                json=json,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401 and not reauthenticated:
                # Token revoked or expired early; fetch a new one and try once more.
                reauthenticated = True
                await self.access_token(force_refresh=True)
                continue
            if response.status_code == 429 and rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                rate_limit_retries += 1
                await asyncio.sleep(_retry_after(response, rate_limit_retries))
                continue
            _raise_for_error(response)
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                return response.text

    async def _get(self, path: str, **params: Any) -> Any:
        return await self.request(
            "GET", path, params={k: v for k, v in params.items() if v is not None}
        )

    # Account

    async def switch_online(self) -> None:
        await self.request("PUT", "/api/offerUser/me/switchOnline")

    async def switch_offline(self) -> None:
        await self.request("PUT", "/api/offerUser/me/switchOffline")

    # Notifications

    async def unread_notifications(self, page_size: int = 50) -> list[dict]:
        page = await self._get(
            "/api/notifications/me", notificationReadStatuses="IsUnread", pageSize=page_size
        )
        return page.get("results") or []

    async def unread_notification_count(self) -> dict:
        return await self._get("/api/notifications/me/unread-count")

    async def mark_all_notifications_read(self) -> None:
        await self.request("PUT", "/api/notifications/me/markAllAsRead")

    # Orders

    async def orders(self, *, state: str | None = None, page_size: int = 25) -> list[dict]:
        page = await self._get(
            "/api/v1/orders/me/seller/orders",
            displayFilter="DisplaySellingOrders",
            orderState=state,
            pageSize=page_size,
        )
        return page.get("results") or []

    async def deliver_order(self, order_id: str) -> dict:
        return await self.request("PUT", f"/api/v1/orders/me/{order_id}/deliver")

    async def cancel_order(self, order_id: str, reason: str, message: str | None = None) -> dict:
        return await self.request(
            "POST",
            f"/api/v1/orders/me/{order_id}/cancel",
            json={"reason": reason, "message": message},
        )

    async def extend_delivery_time(
        self, order_id: str, time: str, reason: str, message: str | None = None
    ) -> None:
        await self.request(
            "POST",
            f"/api/v1/orders/me/{order_id}/extend-delivery-time",
            json={"time": time, "reason": reason, "message": message},
        )

    async def request_details_correction(self, order_id: str, message: str) -> dict:
        return await self.request(
            "POST",
            f"/api/v1/orders/me/{order_id}/delivery-details/correction",
            json={"message": message},
        )

    # Item offers

    async def item_offers(
        self,
        *,
        query: str | None = None,
        state: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        return await self._get(
            "/api/v1/item-management/me/offers/me/search",
            searchQuery=query or None,
            offerState=state,
            pageIndex=page,
            pageSize=page_size,
        )

    async def pause_item_offer(self, offer_id: str) -> None:
        await self.request("POST", f"/api/v1/item-management/me/offers/{offer_id}/pause")

    async def resume_item_offer(self, offer_id: str) -> None:
        await self.request("POST", f"/api/v1/item-management/me/offers/{offer_id}/resume")

    async def set_item_offer_price(self, offer_id: str, amount: float, currency: str) -> dict:
        return await self.request(
            "PUT",
            f"/api/v1/item-management/me/offers/{offer_id}/price",
            json={"amount": amount, "currency": currency},
        )

    async def set_item_offer_quantity(self, offer_id: str, quantity: int) -> Any:
        return await self.request(
            "PUT", f"/api/v1/item-management/me/offers/{offer_id}/quantity", json=quantity
        )

    async def delete_item_offer(self, offer_id: str) -> None:
        await self.request("DELETE", f"/api/v1/item-management/me/offers/{offer_id}")

    # Account offers ("flexible offers" in the API)

    async def account_offers(
        self,
        *,
        query: str | None = None,
        state: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        return await self._get(
            "/api/flexibleOffers/me/search",
            searchQuery=query or None,
            offerState=state,
            category="Account",
            pageIndex=page,
            pageSize=page_size,
        )

    async def pause_account_offer(self, offer_id: str) -> None:
        await self.request("PUT", f"/api/flexibleOffersUser/me/{offer_id}/pause")

    async def resume_account_offer(self, offer_id: str) -> None:
        await self.request("PUT", f"/api/flexibleOffersUser/me/{offer_id}/resume")

    async def set_account_offer_price(self, offer_id: str, amount: float, currency: str) -> dict:
        return await self.request(
            "PUT",
            f"/api/flexibleOffersUser/me/{offer_id}/changePrice",
            json={"amount": amount, "currency": currency},
        )

    async def delete_account_offer(self, offer_id: str) -> None:
        await self.request("DELETE", f"/api/flexibleOffersUser/me/{offer_id}")

    # Boosting

    async def boosting_requests(
        self, *, filter: str = "ActiveRequests", page_size: int = 25
    ) -> list[dict]:
        page = await self._get(
            "/api/boostingOffers/me/boostingRequests/received", filter=filter, pageSize=page_size
        )
        return page.get("results") or []

    async def boosting_request(self, request_id: str) -> dict:
        # Used by the Eldorado website but absent from the Seller API reference,
        # so callers must cope with it failing.
        return await self._get(f"/api/boostingOffers/boostingRequests/{request_id}")

    async def boosting_subscriptions(self) -> list[dict]:
        return await self._get("/api/boostingOffers/me/boostingSubscriptions")

    async def create_boosting_offer(
        self,
        request_id: str,
        amount: float,
        currency: str,
        delivery_time: str,
        message: str | None = None,
    ) -> dict:
        return await self.request(
            "POST",
            "/api/boostingOffers",
            json={
                "details": {
                    "boostingRequestId": request_id,
                    "guaranteedDeliveryTime": delivery_time,
                    "pricing": {
                        "quantity": 1,
                        "minQuantity": 1,
                        "pricePerUnit": {"amount": amount, "currency": currency},
                    },
                    "message": message,
                }
            },
        )

    async def delete_boosting_offer(self, offer_id: str) -> None:
        await self.request("DELETE", f"/api/boostingOffers/{offer_id}/delete")


def _retry_after(response: httpx.Response, attempt: int) -> float:
    try:
        delay = float(response.headers.get("Retry-After", ""))
    except ValueError:
        delay = 2.0**attempt
    return min(max(delay, 0.0), MAX_RETRY_AFTER)
