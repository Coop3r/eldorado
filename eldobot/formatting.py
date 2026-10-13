import re

import discord

SITE = "https://www.eldorado.gg"
BOOSTING_REQUESTS_URL = f"{SITE}/dashboard/boosting/received-requests"

STATE_COLOURS = {
    "Paid": discord.Colour.green(),
    "PendingReview": discord.Colour.gold(),
    "Delivered": discord.Colour.blue(),
    "Received": discord.Colour.blue(),
    "Completed": discord.Colour.dark_green(),
    "Disputed": discord.Colour.red(),
    "DeliveredDisputed": discord.Colour.red(),
    "Canceled": discord.Colour.dark_grey(),
}
DELIVERY_TIME_LABELS = {"Instant": "Instant", "Automated": "Automated", "NotApplicable": "n/a"}


def money(value: dict | None) -> str:
    if not value:
        return "?"
    amount, currency = value.get("amount", 0), value.get("currency", "")
    return f"${amount:,.2f}" if currency == "USD" else f"{amount:,.2f} {currency}"


def humanize(name: str) -> str:
    """'OrderCancelledBySeller' / 'Out_Of_Stock' -> 'Order cancelled by seller' / 'Out of stock'."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name.replace("_", " ")).split()
    return " ".join(words).capitalize()


def delivery_time(value: str | None) -> str:
    if not value:
        return "?"
    if value in DELIVERY_TIME_LABELS:
        return DELIVERY_TIME_LABELS[value]
    match = re.fullmatch(r"(Minute|Hour|Day)(\d+)", value)
    return f"{match[2]} {match[1].lower()}{'s' if match[2] != '1' else ''}" if match else value


def order_url(order_id: str) -> str:
    return f"{SITE}/order/{order_id}"


def order_title(order: dict) -> str:
    details = order.get("orderOfferDetails") or {}
    return details.get("offerTitle") or f"{details.get('gameCategoryTitle') or 'Order'}"


def order_embed(order: dict, heading: str) -> discord.Embed:
    details = order.get("orderOfferDetails") or {}
    state = (order.get("state") or {}).get("state", "?")
    embed = discord.Embed(
        title=f"{heading}: {order_title(order)}"[:256],
        url=order_url(order["id"]),
        colour=STATE_COLOURS.get(state, discord.Colour.blurple()),
    )
    embed.add_field(name="Buyer", value=order.get("buyerUsername") or "?")
    embed.add_field(name="Total", value=money(order.get("totalPrice")))
    embed.add_field(name="Quantity", value=str(order.get("purchaseQuantity", "?")))
    embed.add_field(name="Type", value=humanize(details.get("category") or "?"))
    embed.add_field(name="Delivery time", value=delivery_time(details.get("guaranteedDeliveryTime")))
    embed.add_field(name="State", value=humanize(state))
    if state == "Paid":
        embed.description = (
            "Message the buyer in the order chat. Eldorado can cancel item orders "
            "if the seller doesn't reply within 30 minutes."
        )
    dispute = order.get("latestDispute")
    if dispute and state in ("Disputed", "DeliveredDisputed"):
        embed.description = f"Dispute: {humanize(dispute.get('reason') or '?')}\n{dispute.get('message') or ''}"[:4000]
    cancelation = order.get("cancelation")
    if cancelation and state == "Canceled":
        embed.description = (
            f"Cancelled by {str(cancelation.get('canceledByUserRole', '?')).lower()}: "
            f"{humanize(cancelation.get('reason') or '?')}"
        )
    embed.set_footer(text=f"Order {order['id']}")
    return embed


def notification_id(item: dict) -> str | None:
    inner = item.get("notification") or item.get("customNotification") or {}
    return inner.get("id")


def notification_event(item: dict) -> str:
    return (item.get("notification") or {}).get("event") or "Custom"


def notification_embed(item: dict) -> discord.Embed:
    custom = item.get("customNotification")
    if custom:
        embed = discord.Embed(
            title=(custom.get("subject") or "Eldorado notification")[:256],
            description=(custom.get("text") or "")[:4000],
            colour=discord.Colour.blurple(),
        )
        url = custom.get("redirectUrl") or ""
        if url.startswith("/"):
            url = SITE + url
        if url.startswith("https://"):
            embed.url = url
        return embed

    notification = item["notification"]
    event = notification.get("event") or "Notification"
    details = notification.get("details") or {}
    embed = discord.Embed(title=humanize(event), colour=discord.Colour.blurple())
    if event == "BoostingRequestCreated":
        embed.title = "New boosting request"
        embed.url = BOOSTING_REQUESTS_URL
        embed.colour = discord.Colour.purple()
        embed.description = "Use `/boost requests` to see it and `/boost offer` to bid."
        # Fallback when the full request can't be fetched: the buyer's raw answers.
        answers = (notification.get("customNotificationData") or {}).get("requestFields") or []
        if answers:
            embed.add_field(name="Request", value=", ".join(map(str, answers))[:1024], inline=False)
    if details.get("title"):
        embed.add_field(name="Offer", value=details["title"][:1024])
    if details.get("gameCategoryTitle"):
        embed.add_field(name="Category", value=details["gameCategoryTitle"])
    if details.get("buyerUsername"):
        embed.add_field(name="Buyer", value=details["buyerUsername"])
    if (details.get("price") or {}).get("amount"):
        embed.add_field(name="Price", value=money(details["price"]))
    return embed


def boosting_request_embed(request: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"New boosting request: {request.get('gameName') or '?'} · "
        f"{request.get('gameCategoryTitle') or '?'}"[:256],
        url=BOOSTING_REQUESTS_URL,
        colour=discord.Colour.purple(),
    )
    values = (request.get("boostingRequestDetails") or {}).get("descriptionValues") or []
    for entry in values[:20]:
        if entry.get("value"):
            long = len(str(entry["value"])) > 40
            embed.add_field(
                name=(entry.get("label") or "Detail")[:256],
                value=str(entry["value"])[:1024],
                inline=not long,
            )
    buyer = (request.get("buyerInfo") or {}).get("user") or {}
    feedback = (request.get("buyerInfo") or {}).get("userOrderInfo") or {}
    ratings = feedback.get("ratingCount") or 0
    rating = f" ({feedback.get('positiveCount', 0)}/{ratings} positive)" if ratings else " (no ratings)"
    embed.add_field(name="Buyer", value=f"{buyer.get('username') or '?'}{rating}")
    if request.get("desiredPrice"):
        embed.add_field(name="Desired price", value=money(request["desiredPrice"]))
    offers = request.get("offers")
    if offers:
        embed.add_field(name="Offers so far", value=str(len(offers)))
    embed.description = "Use `/boost offer` to bid."
    embed.set_footer(text=f"Request {request.get('id', '?')}")
    return embed


def offer_line(offer: dict) -> str:
    title = offer.get("offerTitle") or offer.get("gameCategoryTitle") or "Untitled"
    return (
        f"**{title}** · {money(offer.get('pricePerUnit'))} · qty {offer.get('quantity', '?')} · "
        f"{offer.get('offerState', '?')} · {delivery_time(offer.get('guaranteedDeliveryTime'))}\n"
        f"`{offer['id']}`"
    )
