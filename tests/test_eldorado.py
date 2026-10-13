import json
import unittest

import httpx

from eldobot.eldorado import TOKEN_PATH, EldoradoClient, EldoradoError


def make_client(handler) -> EldoradoClient:
    return EldoradoClient("id", "secret", transport=httpx.MockTransport(handler))


class EldoradoClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_is_cached_and_sent_as_bearer(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path, request.headers.get("Authorization")))
            if request.url.path == TOKEN_PATH:
                return httpx.Response(200, json={"accessToken": "tok-1", "expiresIn": 900})
            return httpx.Response(200, json={"ok": True})

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        self.assertEqual(await client.request("GET", "/api/a"), {"ok": True})
        await client.request("GET", "/api/b")

        self.assertEqual([c[1] for c in calls], [TOKEN_PATH, "/api/a", "/api/b"])
        self.assertEqual(calls[1][2], "Bearer tok-1")

    async def test_expired_token_is_refreshed(self):
        issued = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == TOKEN_PATH:
                issued.append(f"tok-{len(issued) + 1}")
                # Shorter than the refresh margin, so it counts as already stale.
                return httpx.Response(200, json={"accessToken": issued[-1], "expiresIn": 1})
            return httpx.Response(200, json={})

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        await client.request("GET", "/api/a")
        await client.request("GET", "/api/a")
        self.assertEqual(issued, ["tok-1", "tok-2"])

    async def test_401_triggers_one_reauthentication(self):
        issued = []
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == TOKEN_PATH:
                issued.append(f"tok-{len(issued) + 1}")
                return httpx.Response(200, json={"accessToken": issued[-1]})
            seen.append(request.headers["Authorization"])
            if request.headers["Authorization"] == "Bearer tok-1":
                return httpx.Response(401, json={"code": 401, "messages": ["expired"]})
            return httpx.Response(204)

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        self.assertIsNone(await client.request("PUT", "/api/orders/me/abc/delivered"))
        self.assertEqual(seen, ["Bearer tok-1", "Bearer tok-2"])

    async def test_persistent_401_raises_instead_of_looping(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == TOKEN_PATH:
                return httpx.Response(200, json={"accessToken": "tok"})
            return httpx.Response(401, json={"code": 401, "messages": ["nope"]})

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        with self.assertRaises(EldoradoError) as ctx:
            await client.request("GET", "/api/a")
        self.assertEqual(ctx.exception.status, 401)

    async def test_bad_credentials_surface_eldorado_message(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401, json={"code": 401, "messages": ["Authentication failed."], "correlationId": None}
            )

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        with self.assertRaises(EldoradoError) as ctx:
            await client.access_token()
        self.assertEqual(ctx.exception.messages, ["Authentication failed."])

    async def test_unknown_token_shape_does_not_leak_values(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"jwt": "super-secret"})

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        with self.assertRaises(EldoradoError) as ctx:
            await client.access_token()
        self.assertIn("jwt", str(ctx.exception))
        self.assertNotIn("super-secret", str(ctx.exception))

    async def test_write_calls_use_documented_endpoints_and_bodies(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == TOKEN_PATH:
                return httpx.Response(200, json={"accessToken": "tok"})
            body = json.loads(request.content) if request.content else None
            seen.append((request.method, request.url.path, body))
            return httpx.Response(200, json={})

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        await client.deliver_order("o1")
        await client.cancel_order("o1", "Out_Of_Stock", "sorry")
        await client.set_item_offer_price("i1", 12.5, "USD")
        await client.set_item_offer_quantity("i1", 0)
        await client.pause_account_offer("a1")
        await client.create_boosting_offer("r1", 20, "USD", "Day1", "hi")

        self.assertEqual(
            seen[:5],
            [
                ("PUT", "/api/v1/orders/me/o1/deliver", None),
                ("POST", "/api/v1/orders/me/o1/cancel", {"reason": "Out_Of_Stock", "message": "sorry"}),
                ("PUT", "/api/v1/item-management/me/offers/i1/price", {"amount": 12.5, "currency": "USD"}),
                ("PUT", "/api/v1/item-management/me/offers/i1/quantity", 0),
                ("PUT", "/api/flexibleOffersUser/me/a1/pause", None),
            ],
        )
        method, path, body = seen[5]
        self.assertEqual((method, path), ("POST", "/api/boostingOffers"))
        self.assertEqual(body["details"]["boostingRequestId"], "r1")
        self.assertEqual(body["details"]["pricing"]["pricePerUnit"], {"amount": 20, "currency": "USD"})

    async def test_list_calls_drop_unset_filters(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == TOKEN_PATH:
                return httpx.Response(200, json={"accessToken": "tok"})
            seen.append((request.url.path, dict(request.url.params)))
            return httpx.Response(200, json={"results": []})

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        await client.orders()
        await client.item_offers(query="rod", state="Active")
        self.assertEqual(
            seen,
            [
                (
                    "/api/v1/orders/me/seller/orders",
                    {"displayFilter": "DisplaySellingOrders", "pageSize": "25"},
                ),
                (
                    "/api/v1/item-management/me/offers/me/search",
                    {"searchQuery": "rod", "offerState": "Active", "pageIndex": "1", "pageSize": "10"},
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
