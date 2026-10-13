"""Slash commands. Every command that changes a live offer or order asks for confirmation."""

from collections.abc import Awaitable, Callable
from typing import Any

import discord
from discord import app_commands

from .formatting import (
    BOOSTING_REQUESTS_URL,
    delivery_time,
    humanize,
    money,
    offer_line,
    order_title,
    order_url,
)

CURRENCY = "USD"
PAGE_SIZE = 10

OFFER_STATES = [app_commands.Choice(name=s, value=s) for s in ("Active", "Paused", "Closed")]
ORDER_STATES = [
    app_commands.Choice(name=humanize(s), value=s)
    for s in ("Paid", "PendingReview", "Delivered", "Disputed", "Received", "Completed", "Canceled")
]
CANCEL_REASONS = [
    app_commands.Choice(name=humanize(s), value=s)
    for s in (
        "Mutual_Agreement",
        "Out_Of_Stock",
        "Buyer_Did_Not_Respond",
        "Buyer_Does_Not_Need_It_Anymore",
        "Buyer_Does_Not_Meet_Criteria_For_Order",
        "Buyer_Provided_Incorrect_Account_Information",
        "Buyer_Asked_For_A_Refund_Due_To_Dissatisfaction",
        "Boost_Couldnt_Be_Completed_Due_To_Account_Issues",
        "Poor_Boosting_Quality",
        "Failed_To_Meet_Timeline",
        "Account_Listed_Incorrectly",
        "Access_To_Account_Lost",
        "Seller_Going_Offline",
        "Other",
    )
]
EXTEND_TIMES = [
    app_commands.Choice(name=delivery_time(s), value=s)
    for s in ("Minute5", "Minute15", "Minute30", "Hour1", "Hour3", "Hour6")
]
EXTEND_REASONS = [
    app_commands.Choice(name=humanize(s), value=s)
    for s in ("WaitingForBuyer", "InGameCoordination", "PreparationDelay", "TechnicalIssue")
]
BOOST_DELIVERY_TIMES = [
    app_commands.Choice(name=delivery_time(s), value=s)
    for s in (
        "Minute20", "Hour1", "Hour2", "Hour3", "Hour5", "Hour8", "Hour12",
        "Day1", "Day2", "Day3", "Day5", "Day7", "Day10", "Day14", "Day28",
    )
]  # fmt: skip
BOOST_FILTERS = [
    app_commands.Choice(name=humanize(s), value=s)
    for s in ("ActiveRequests", "OfferSubmitted", "OfferWon", "OfferLost")
]


class ConfirmView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()


async def confirmed_action(
    interaction: discord.Interaction,
    prompt: str,
    action: Callable[[], Awaitable[Any]],
    done: str | Callable[[Any], str],
) -> None:
    """Ask for confirmation, run the action, and report the outcome in the same message."""
    view = ConfirmView(interaction.user.id)
    await interaction.response.send_message(prompt, view=view, ephemeral=True)
    await view.wait()
    if not view.confirmed:
        await interaction.edit_original_response(content="Cancelled, nothing changed.", view=None)
        return
    await interaction.edit_original_response(content="Working…", view=None)
    # Errors propagate to the tree's on_error, which reports them.
    result = await action()
    await interaction.edit_original_response(content=done(result) if callable(done) else done)


def _choice(label: str, value: str) -> app_commands.Choice[str]:
    return app_commands.Choice(name=label[:100], value=value)


def offer_autocomplete(search_method: str):
    async def autocomplete(interaction: discord.Interaction, current: str):
        try:
            search = getattr(interaction.client.eldorado, search_method)
            page = await search(query=current, page_size=25)
        except Exception:
            return []
        return [
            _choice(
                f"{o.get('offerTitle') or 'Untitled'} · {money(o.get('pricePerUnit'))} · {o.get('offerState')}",
                o["id"],
            )
            for o in page.get("results") or []
        ]

    return autocomplete


async def order_autocomplete(interaction: discord.Interaction, current: str):
    try:
        orders = await interaction.client.eldorado.orders(page_size=25)
    except Exception:
        return []
    choices = []
    for order in orders:
        state = (order.get("state") or {}).get("state", "?")
        label = f"{order_title(order)} · {order.get('buyerUsername')} · {humanize(state)}"
        if current.lower() in label.lower() or current.lower() in order["id"].lower():
            choices.append(_choice(label, order["id"]))
    return choices


async def boost_request_autocomplete(interaction: discord.Interaction, current: str):
    try:
        requests = await interaction.client.eldorado.boosting_requests()
    except Exception:
        return []
    choices = []
    for request in requests:
        label = f"{request.get('boostingCategoryTitle')} · {request.get('buyerUsername')} · {request.get('createdDate', '')[:16].replace('T', ' ')}"
        if current.lower() in label.lower() or current.lower() in request["id"].lower():
            choices.append(_choice(label, request["id"]))
    return choices


def offers_embed(heading: str, page: dict) -> discord.Embed:
    results = page.get("results") or []
    embed = discord.Embed(
        title=heading,
        description="\n\n".join(offer_line(o) for o in results) or "No offers found.",
        colour=discord.Colour.blurple(),
    )
    embed.set_footer(
        text=f"Page {page.get('pageIndex', 1)} of {max(page.get('totalPages', 1), 1)} · "
        f"{page.get('recordCount', len(results))} offers"
    )
    return embed


def offer_group(
    name: str, label: str, *, search: str, pause: str, resume: str, price: str, delete: str
) -> app_commands.Group:
    """Commands shared by item and account offers; the arguments name the client methods."""
    group = app_commands.Group(name=name, description=f"Manage your {label} offers.")
    autocomplete = offer_autocomplete(search)

    @group.command(name="list", description=f"List your {label} offers.")
    @app_commands.describe(query="Search text", state="Only offers in this state", page="Page number")
    @app_commands.choices(state=OFFER_STATES)
    async def list_(
        interaction: discord.Interaction,
        query: str | None = None,
        state: app_commands.Choice[str] | None = None,
        page: app_commands.Range[int, 1] = 1,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await getattr(interaction.client.eldorado, search)(
            query=query, state=state.value if state else None, page=page, page_size=PAGE_SIZE
        )
        await interaction.followup.send(embed=offers_embed(f"{label.capitalize()} offers", result))

    @group.command(name="pause", description=f"Pause one {label} offer.")
    @app_commands.autocomplete(offer=autocomplete)
    async def pause_(interaction: discord.Interaction, offer: str) -> None:
        await confirmed_action(
            interaction,
            f"Pause {label} offer `{offer}`?",
            lambda: getattr(interaction.client.eldorado, pause)(offer),
            f"Paused `{offer}`.",
        )

    @group.command(name="resume", description=f"Resume one paused {label} offer.")
    @app_commands.autocomplete(offer=autocomplete)
    async def resume_(interaction: discord.Interaction, offer: str) -> None:
        await confirmed_action(
            interaction,
            f"Resume {label} offer `{offer}`?",
            lambda: getattr(interaction.client.eldorado, resume)(offer),
            f"Resumed `{offer}`.",
        )

    @group.command(name="price", description=f"Change the price of one {label} offer (USD).")
    @app_commands.autocomplete(offer=autocomplete)
    @app_commands.describe(amount="New price per unit in USD")
    async def price_(
        interaction: discord.Interaction, offer: str, amount: app_commands.Range[float, 0.01]
    ) -> None:
        await confirmed_action(
            interaction,
            f"Set the price of {label} offer `{offer}` to **${amount:,.2f}**?",
            lambda: getattr(interaction.client.eldorado, price)(offer, amount, CURRENCY),
            f"Price of `{offer}` is now ${amount:,.2f}.",
        )

    @group.command(name="delete", description=f"Permanently delete one {label} offer.")
    @app_commands.autocomplete(offer=autocomplete)
    async def delete_(interaction: discord.Interaction, offer: str) -> None:
        await confirmed_action(
            interaction,
            f"**Permanently delete** {label} offer `{offer}`? This cannot be undone.",
            lambda: getattr(interaction.client.eldorado, delete)(offer),
            f"Deleted `{offer}`.",
        )

    return group


def register_commands(tree: app_commands.CommandTree) -> None:
    @tree.command(description="Check the bot's connection to Eldorado.")
    async def status(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        eldorado = interaction.client.eldorado
        unread = await eldorado.unread_notification_count()
        count = f"{unread.get('unread', 0)}{'+' if unread.get('hasMore') else ''}"
        await interaction.followup.send(
            f"Connected to Eldorado. {count} unread notifications. "
            f"Discord latency {interaction.client.latency * 1000:.0f} ms."
        )

    @tree.command(description="Show your offers as online.")
    async def online(interaction: discord.Interaction) -> None:
        await confirmed_action(
            interaction,
            "Switch your Eldorado seller status to **online**?",
            interaction.client.eldorado.switch_online,
            "You are now online.",
        )

    @tree.command(description="Take your offers offline.")
    async def offline(interaction: discord.Interaction) -> None:
        await confirmed_action(
            interaction,
            "Switch your Eldorado seller status to **offline**? Buyers won't see your offers.",
            interaction.client.eldorado.switch_offline,
            "You are now offline.",
        )

    notifications = app_commands.Group(name="notifications", description="Eldorado notifications.")

    @notifications.command(name="read", description="Mark all Eldorado notifications as read.")
    async def notifications_read(interaction: discord.Interaction) -> None:
        await confirmed_action(
            interaction,
            "Mark all Eldorado notifications as read?",
            interaction.client.eldorado.mark_all_notifications_read,
            "All notifications marked as read.",
        )

    tree.add_command(notifications)

    tree.add_command(
        offer_group(
            "items",
            "item",
            search="item_offers",
            pause="pause_item_offer",
            resume="resume_item_offer",
            price="set_item_offer_price",
            delete="delete_item_offer",
        )
    )
    items = tree.get_command("items")

    @items.command(name="quantity", description="Change the stock of one item offer.")
    @app_commands.autocomplete(offer=offer_autocomplete("item_offers"))
    async def items_quantity(
        interaction: discord.Interaction, offer: str, quantity: app_commands.Range[int, 0]
    ) -> None:
        await confirmed_action(
            interaction,
            f"Set the quantity of item offer `{offer}` to **{quantity}**?",
            lambda: interaction.client.eldorado.set_item_offer_quantity(offer, quantity),
            f"Quantity of `{offer}` is now {quantity}.",
        )

    tree.add_command(
        offer_group(
            "accounts",
            "account",
            search="account_offers",
            pause="pause_account_offer",
            resume="resume_account_offer",
            price="set_account_offer_price",
            delete="delete_account_offer",
        )
    )

    orders = app_commands.Group(name="orders", description="Manage your Eldorado orders.")

    @orders.command(name="list", description="List your recent sales.")
    @app_commands.choices(state=ORDER_STATES)
    async def orders_list(
        interaction: discord.Interaction, state: app_commands.Choice[str] | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        results = await interaction.client.eldorado.orders(
            state=state.value if state else None, page_size=PAGE_SIZE
        )
        lines = [
            f"[**{order_title(o)}**]({order_url(o['id'])}) · {o.get('buyerUsername')} · "
            f"{money(o.get('totalPrice'))} · {humanize((o.get('state') or {}).get('state', '?'))}\n"
            f"`{o['id']}`"
            for o in results
        ]
        embed = discord.Embed(
            title="Recent orders",
            description="\n\n".join(lines) or "No orders found.",
            colour=discord.Colour.blurple(),
        )
        await interaction.followup.send(embed=embed)

    @orders.command(name="deliver", description="Mark an order as delivered.")
    @app_commands.autocomplete(order=order_autocomplete)
    async def orders_deliver(interaction: discord.Interaction, order: str) -> None:
        await confirmed_action(
            interaction,
            f"Mark order `{order}` as **delivered**?",
            lambda: interaction.client.eldorado.deliver_order(order),
            f"Order `{order}` marked as delivered.",
        )

    @orders.command(name="cancel", description="Cancel an order and refund the buyer.")
    @app_commands.autocomplete(order=order_autocomplete)
    @app_commands.choices(reason=CANCEL_REASONS)
    async def orders_cancel(
        interaction: discord.Interaction,
        order: str,
        reason: app_commands.Choice[str],
        message: str | None = None,
    ) -> None:
        await confirmed_action(
            interaction,
            f"**Cancel** order `{order}` (reason: {reason.name})? The buyer is refunded.",
            lambda: interaction.client.eldorado.cancel_order(order, reason.value, message),
            f"Order `{order}` cancelled.",
        )

    @orders.command(name="extend", description="Extend an order's delivery time.")
    @app_commands.autocomplete(order=order_autocomplete)
    @app_commands.choices(time=EXTEND_TIMES, reason=EXTEND_REASONS)
    async def orders_extend(
        interaction: discord.Interaction,
        order: str,
        time: app_commands.Choice[str],
        reason: app_commands.Choice[str],
        message: str | None = None,
    ) -> None:
        await confirmed_action(
            interaction,
            f"Extend delivery of order `{order}` by **{time.name}** ({reason.name})?",
            lambda: interaction.client.eldorado.extend_delivery_time(
                order, time.value, reason.value, message
            ),
            f"Delivery time of `{order}` extended by {time.name}.",
        )

    @orders.command(
        name="fix-details", description="Ask the buyer to correct their delivery details."
    )
    @app_commands.autocomplete(order=order_autocomplete)
    @app_commands.describe(message="What the buyer needs to correct")
    async def orders_fix_details(interaction: discord.Interaction, order: str, message: str) -> None:
        await confirmed_action(
            interaction,
            f"Ask the buyer of order `{order}` to correct their delivery details?\n> {message}",
            lambda: interaction.client.eldorado.request_details_correction(order, message),
            f"Correction requested on order `{order}`.",
        )

    tree.add_command(orders)

    boost = app_commands.Group(name="boost", description="Boosting requests and offers.")

    @boost.command(name="requests", description="List boosting requests you have received.")
    @app_commands.choices(filter=BOOST_FILTERS)
    async def boost_requests(
        interaction: discord.Interaction, filter: app_commands.Choice[str] | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        results = await interaction.client.eldorado.boosting_requests(
            filter=filter.value if filter else "ActiveRequests", page_size=PAGE_SIZE
        )
        lines = [
            f"**{r.get('boostingCategoryTitle')}** · {r.get('buyerUsername')} · "
            f"{r.get('createdDate', '')[:16].replace('T', ' ')} UTC\n`{r['id']}`"
            for r in results
        ]
        embed = discord.Embed(
            title=f"Boosting requests: {filter.name if filter else 'Active requests'}",
            url=BOOSTING_REQUESTS_URL,
            description="\n\n".join(lines) or "No requests found.",
            colour=discord.Colour.purple(),
        )
        await interaction.followup.send(embed=embed)

    @boost.command(name="offer", description="Send an offer on a boosting request (USD).")
    @app_commands.autocomplete(request=boost_request_autocomplete)
    @app_commands.choices(delivery=BOOST_DELIVERY_TIMES)
    @app_commands.describe(price="Your price in USD", message="Message shown to the buyer")
    async def boost_offer(
        interaction: discord.Interaction,
        request: str,
        price: app_commands.Range[float, 0.01],
        delivery: app_commands.Choice[str],
        message: str | None = None,
    ) -> None:
        await confirmed_action(
            interaction,
            f"Send a **${price:,.2f}** offer ({delivery.name}) on boosting request `{request}`?",
            lambda: interaction.client.eldorado.create_boosting_offer(
                request, price, CURRENCY, delivery.value, message
            ),
            lambda offer: (
                f"Offer sent: ${price:,.2f}, {delivery.name}. "
                f"Offer ID `{(offer or {}).get('id', '?')}` (needed for `/boost withdraw`)."
            ),
        )

    @boost.command(name="withdraw", description="Delete an offer you sent on a boosting request.")
    @app_commands.describe(offer_id="The offer ID shown when you sent the offer")
    async def boost_withdraw(interaction: discord.Interaction, offer_id: str) -> None:
        await confirmed_action(
            interaction,
            f"Withdraw boosting offer `{offer_id}`?",
            lambda: interaction.client.eldorado.delete_boosting_offer(offer_id.strip()),
            f"Boosting offer `{offer_id}` withdrawn.",
        )

    @boost.command(name="subscriptions", description="Show the boosting categories you receive requests for.")
    async def boost_subscriptions(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        subscriptions = await interaction.client.eldorado.boosting_subscriptions()
        lines = [
            f"**{s.get('gameName')}** · {s.get('boostingCategoryName')}"
            for s in subscriptions
            if s.get("isSubscribed")
        ]
        embed = discord.Embed(
            title="Boosting subscriptions",
            description="\n".join(lines)[:4000] or "You aren't subscribed to any boosting categories.",
            colour=discord.Colour.purple(),
        )
        await interaction.followup.send(embed=embed)

    tree.add_command(boost)
