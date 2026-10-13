import unittest
from datetime import datetime, timedelta, timezone

from eldobot.eldorado import EldoradoError
from eldobot.poller import Poller
from eldobot.store import Store


def order(id: str, state: str) -> dict:
    return {
        "id": id,
        "state": {"state": state},
        "buyerUsername": "buyer",
        "purchaseQuantity": 1,
        "totalPrice": {"amount": 10.0, "currency": "USD"},
        "orderOfferDetails": {
            "offerTitle": "Thing",
            "category": "CustomItem",
            "guaranteedDeliveryTime": "Hour12",
        },
    }


def notification(id: str, event: str, age: timedelta = timedelta(minutes=1)) -> dict:
    sent_at = datetime.now(timezone.utc) - age
    return {
        "customNotification": None,
        "notification": {
            "id": id,
            # Eldorado sends seven fractional digits and a Z suffix.
            "notificationDate": sent_at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z",
            "type": "ActionRequired",
            "event": event,
            "details": {
                "detailsId": f"req-{id}",
                "buyerUsername": "buyer",
                "gameCategoryTitle": "Power Leveling",
            },
            "customNotificationData": {"requestFields": ["833000", "iOS"]},
        },
    }


class FakeEldorado:
    def __init__(self):
        self.order_list: list[dict] = []
        self.notification_list: list[dict] = []
        self.request_details: dict | None = None

    async def orders(self, **_):
        return self.order_list

    async def unread_notifications(self, **_):
        return self.notification_list

    async def boosting_request(self, request_id):
        if self.request_details is None:
            raise EldoradoError(403, ["forbidden"])
        return {"id": request_id, **self.request_details}


class PollerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.eldorado = FakeEldorado()
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.sent: list[tuple[str, bool]] = []

        async def send(embed, ping_owner):
            self.sent.append((embed.title, ping_owner))
            self.embeds.append(embed)

        self.embeds = []

        self.poller = Poller(self.eldorado, self.store, send)

    async def test_first_run_is_silent_then_only_changes_are_announced(self):
        self.eldorado.order_list = [order("o1", "Completed")]
        self.eldorado.notification_list = [notification("n1", "BoostingRequestCreated")]
        await self.poller.tick()
        self.assertEqual(self.sent, [])

        await self.poller.tick()
        self.assertEqual(self.sent, [])

        self.eldorado.order_list = [order("o2", "Paid"), order("o1", "Completed")]
        self.eldorado.notification_list.insert(0, notification("n2", "BoostingRequestCreated"))
        await self.poller.tick()
        self.assertEqual(
            self.sent, [("New order: Thing", True), ("New boosting request", False)]
        )

        self.sent.clear()
        self.eldorado.order_list = [order("o2", "Disputed"), order("o1", "Completed")]
        await self.poller.tick()
        self.assertEqual(self.sent, [("Order disputed: Thing", True)])

    async def test_first_order_after_an_empty_first_run_is_announced(self):
        await self.poller.tick()
        self.eldorado.order_list = [order("o1", "Paid")]
        await self.poller.tick()
        self.assertEqual(self.sent, [("New order: Thing", True)])

    async def test_order_notifications_are_not_announced_twice(self):
        await self.poller.tick()
        self.eldorado.notification_list = [notification("n1", "OrderCreated")]
        await self.poller.tick()
        self.assertEqual(self.sent, [])

    async def test_stale_notifications_are_not_announced(self):
        await self.poller.tick()
        self.eldorado.notification_list = [
            notification("old", "BoostingRequestCreated", age=timedelta(days=3)),
            notification("new", "BoostingRequestCreated"),
        ]
        await self.poller.tick()
        self.assertEqual(self.sent, [("New boosting request", False)])

    async def test_everything_except_boosting_requests_pings(self):
        await self.poller.tick()
        self.eldorado.order_list = [order("o1", "Completed")]
        self.eldorado.notification_list = [
            notification("n1", "ReviewCompleted"),
            notification("n2", "BoostingRequestCreated"),
        ]
        await self.poller.tick()
        self.assertEqual(
            sorted(self.sent),
            [("New boosting request", False), ("Order completed: Thing", True), ("Review completed", True)],
        )

    async def test_boosting_alert_includes_what_the_buyer_asked_for(self):
        await self.poller.tick()
        self.eldorado.request_details = {
            "gameName": "Pokemon Go",
            "gameCategoryTitle": "Custom Request",
            "boostingRequestDetails": {
                "descriptionValues": [
                    {"label": "Platform", "value": "iOS"},
                    {"label": "Request description", "value": "Need stardust"},
                ]
            },
            "buyerInfo": {"user": {"username": "buyer"}, "userOrderInfo": {"ratingCount": 0}},
        }
        self.eldorado.notification_list = [notification("n1", "BoostingRequestCreated")]
        await self.poller.tick()

        embed = self.embeds[0]
        self.assertEqual(embed.title, "New boosting request: Pokemon Go · Custom Request")
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["Platform"], "iOS")
        self.assertEqual(fields["Request description"], "Need stardust")
        self.assertEqual(embed.footer.text, "Request req-n1")
        self.assertEqual(self.sent[0][1], False)

    async def test_boosting_alert_falls_back_to_raw_answers(self):
        await self.poller.tick()
        self.eldorado.notification_list = [notification("n1", "BoostingRequestCreated")]
        with self.assertLogs("eldobot.poller", level="WARNING"):
            await self.poller.tick()
        fields = {f.name: f.value for f in self.embeds[0].fields}
        self.assertEqual(fields["Request"], "833000, iOS")

    async def test_failed_send_is_retried_on_the_next_tick(self):
        await self.poller.tick()
        self.eldorado.order_list = [order("o1", "Paid")]
        attempts = []

        async def flaky_send(embed, ping_owner):
            attempts.append(embed.title)
            if len(attempts) == 1:
                raise RuntimeError("discord down")

        self.poller._send = flaky_send
        with self.assertLogs("eldobot.poller", level="ERROR"):
            await self.poller.tick()
        await self.poller.tick()
        self.assertEqual(attempts, ["New order: Thing", "New order: Thing"])


if __name__ == "__main__":
    unittest.main()
