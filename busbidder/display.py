"""Builds the text the user sees: request summary, ranking, comparison.

Every function returns lines of text and prints nothing, so the output
can be tested and saved to a file as well as shown on screen.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from busbidder.constraints import FilterResult, advise, describe_constraints
from busbidder.criteria import CRITERIA
from busbidder.explain import explain_top
from busbidder.models import Booking, Departure, TravelRequest, count_of, format_duration
from busbidder.ranking import ScoredOffer, describe_dominance, near_tie_gap

DAY_FORMAT = "%a %d %b %H:%M"
DATE_FORMAT = "%d %B %Y"
RULE = "-" * 72
# A ranked offer gets a note when the bus could not take the party twice,
# so the traveller knows it may sell out. One passenger is warned at the
# last seat, a party of four when fewer than eight seats are left.
LOW_SEATS_FACTOR = 2


def format_when(departure: Departure) -> str:
    """Return departure and arrival as text, with the day when it changes."""
    leaves, arrives = departure.departure, departure.arrival
    arrival_format = "%H:%M" if arrives.date() == leaves.date() else DAY_FORMAT
    return f"{leaves:{DAY_FORMAT}} -> {arrives:{arrival_format}}"


def format_weights(weights: Mapping[str, float]) -> str:
    """Return the weights as text such as 'Price 40%, Duration 25%'."""
    return ", ".join(f"{CRITERIA[name].label} {weight:g}%" for name, weight in weights.items())


def format_request(request: TravelRequest) -> list[str]:
    """Return the request with its mandatory conditions."""
    lines = [
        f"Journey: Madrid to {request.destination.name}, "
        f"{request.date_from} to {request.date_to}, {count_of(request.passengers, 'passenger')}",
        "",
        "Mandatory conditions (an offer that fails one is removed before ranking):",
        f"  - destination {request.destination.name}, departure on {request.date_from} "
        f"to {request.date_to} (these decide which offers are searched)",
    ]
    lines.extend(f"  - {condition}" for condition in describe_constraints(request))
    return lines


def format_preferences(request: TravelRequest, weights: Mapping[str, float], source: str) -> list[str]:
    """Return the scored preferences and where the weights come from."""
    lines = [
        "",
        "Preferences (scored from 0 to 100 and weighted; they never remove an offer):",
        f"  - {format_weights(weights)}",
        f"  - preferred departure: {request.preferred_departure.describe()}",
        f"  - weights from: {source}",
    ]
    dominance = describe_dominance(weights)
    if dominance is not None:
        lines.append(f"  Note: {dominance}. The traveller confirmed this.")
    return lines


def format_coverage(days: tuple[date, date]) -> str:
    """Return the days the data covers, as text."""
    first, last = days
    return f"BusBidder has buses from {first:{DATE_FORMAT}} to {last:{DATE_FORMAT}}."


def format_filter(
    result: FilterResult,
    request: TravelRequest | None = None,
    days: tuple[date, date] | None = None,
) -> list[str]:
    """Return what the filter kept and removed.

    request and days (the first and last day in the data) make the advice
    specific: which dates have buses, and which requirement to change.
    """
    lines = ["", f"Offers for this city and these dates: {result.in_scope}"]
    if result.in_scope == 0:
        lines.append("No buses found for these dates, so there is nothing to rank.")
        lines.append(format_coverage(days) if days else "Try other dates.")
        return lines
    if days and request and (request.date_from < days[0] or request.date_to > days[1]):
        lines.append(f"Note: some of your dates have no buses. {format_coverage(days)}")
    lines.append(f"Removed by a mandatory condition: {len(result.removed)}")
    used = [f"{name} {count}" for name, count in result.counts.items() if count]
    if used:
        lines.append(f"  removed per condition (an offer can fail several): {', '.join(used)}")
    lines.append(f"Offers left to rank: {len(result.accepted)}")
    if not result.accepted:
        lines.append("No buses match all your requirements.")
        if request is not None:
            lines.extend(advise(result, request))
        else:
            strictest = result.strictest()
            lines.append(f"{result.counts[strictest]} of the {result.in_scope} offers fail "
                         f"the condition '{strictest}'.")
    return lines


def format_removed(result: FilterResult) -> list[str]:
    """Return every removed offer with all the reasons it was removed."""
    lines = ["", "Removed offers and the reasons:"]
    for removed in result.removed:
        reasons = "; ".join(removed.reasons.values())
        lines.append(f"  {removed.offer.offer_id}: {reasons}")
    return lines


def format_scored_offer(rank: int, scored: ScoredOffer, weights: Mapping[str, float],
                        seats_left: int, passengers: int = 1) -> list[str]:
    """Return one ranked offer: its values, points, contributions and total."""
    offer = scored.offer
    transfers = offer.departure.transfers
    route = "direct" if transfers == 0 else count_of(transfers, "transfer")
    lines = [
        f"#{rank}  {offer.offer_id}  {offer.company.name}, {offer.fare.fare_type} fare"
        f"   TOTAL {scored.total:.2f}",
        f"    {format_when(offer.departure)} ({format_duration(offer.duration_minutes)}), "
        f"{route}, {count_of(seats_left, 'seat')} left, luggage {offer.fare.luggage_kg} kg",
    ]
    if seats_left < LOW_SEATS_FACTOR * passengers:
        lines.append(f"    Note: only {count_of(seats_left, 'seat')} left on this bus.")
    for name, score in scored.scores.items():
        value = score.value + (" (estimated)" if score.estimated else "")
        lines.append(
            f"    {CRITERIA[name].label:<12}{value:<26}{score.points:>6.1f} pts"
            f" x {weights[name]:>5g}% = {scored.contributions[name]:>6.2f}"
        )
    return lines


def format_near_tie(ranked: Sequence[ScoredOffer]) -> list[str]:
    """Return a note when the first two offers are almost equal, or nothing."""
    gap = near_tie_gap(ranked)
    if gap is None:
        return []
    return [
        f"Note: the first two offers are only {gap:.2f} points apart, so a small change "
        "in the weights can change which one ranks first.",
    ]


def format_ranking(ranked: Sequence[ScoredOffer], weights: Mapping[str, float],
                   seats_left: Mapping[str, int], top: int, passengers: int = 1) -> list[str]:
    """Return the ranked offers, best first, followed by the explanation."""
    if not ranked:
        return []
    shown = ranked if top <= 0 else ranked[:top]
    lines = ["", RULE, "Ranked offers, best first (value, points, weight, contribution)", RULE]
    for rank, scored in enumerate(shown, start=1):
        left = seats_left.get(scored.offer.departure.departure_id, 0)
        lines.extend(format_scored_offer(rank, scored, weights, left, passengers))
        lines.append("")
    if len(shown) < len(ranked):
        lines.append(f"Showing {len(shown)} of {len(ranked)} ranked offers. Use --top 0 to show all.")
        lines.append("")
    lines.append("Why #1 ranks first:")
    lines.extend(f"  {line}" for line in explain_top(ranked))
    lines.extend(f"  {line}" for line in format_near_tie(ranked))
    return lines


def format_comparison(
    results: Mapping[str, tuple[str, Mapping[str, float], Sequence[ScoredOffer]]],
    seats_left: Mapping[str, int],
    passengers: int = 1,
) -> list[str]:
    """Return the top offer of each profile side by side, then each explanation.

    results maps a profile key to (description, weights, ranking).
    """
    lines = ["", RULE, "Profile comparison: same request and same offers, different weights", RULE]
    lines.append(
        f"{'Profile':<10}{'Top offer':<15}{'Company':<16}{'Departs':<18}"
        f"{'Price':>11} {'Duration':<9}{'Fare':<10}{'Total':>6}"
    )
    winners = set()
    for key, (_description, _weights, ranked) in results.items():
        if not ranked:
            lines.append(f"{key:<10}(no offer left to rank)")
            continue
        top = ranked[0]
        offer = top.offer
        winners.add(offer.offer_id)
        lines.append(
            f"{key:<10}{offer.offer_id:<15}{offer.company.name:<16}"
            f"{offer.departure.departure:{DAY_FORMAT}}  {offer.price:>7.2f} EUR"
            f" {format_duration(offer.duration_minutes):<9}{offer.fare.fare_type:<10}{top.total:>6.2f}"
        )
    if winners:
        lines.append("")
        lines.append(
            f"The {len(results)} profiles recommend {count_of(len(winners), 'different offer')}."
        )
    for key, (description, weights, ranked) in results.items():
        lines.extend(["", RULE, f"Profile '{key}': {description}", f"Weights: {format_weights(weights)}", RULE])
        for rank, scored in enumerate(ranked[:1], start=1):
            left = seats_left.get(scored.offer.departure.departure_id, 0)
            lines.extend(format_scored_offer(rank, scored, weights, left, passengers))
        runners = [f"#{rank} {scored.offer.offer_id} ({scored.total:.2f})"
                   for rank, scored in enumerate(ranked[1:3], start=2)]
        if runners:
            lines.append(f"    next: {', '.join(runners)}")
        if ranked:
            lines.append("")
            lines.extend(f"  {line}" for line in explain_top(ranked))
            lines.extend(f"  {line}" for line in format_near_tie(ranked))
    return lines


def format_next_step(ranked: Sequence[ScoredOffer], passengers: int) -> list[str]:
    """Return how to book after a search, or nothing when no offer is ranked."""
    if not ranked:
        return []
    return [
        "",
        "Next step: to book an offer, use its offer ID. For the top offer:",
        f"  python3 main.py book --offer {ranked[0].offer.offer_id} --passengers {passengers}",
    ]


def format_seats_after(seats_left: int) -> str:
    """Return how many seats the bus has left after a booking or a cancellation."""
    if seats_left == 0:
        return "This bus is now sold out."
    return f"Seats left on this bus: {seats_left}"


def format_outcome(booking: Booking) -> list[str]:
    """Return what just happened to the booking, in plain words.

    It is shown first, so the traveller sees the result of the action
    before the details.
    """
    fare_type = booking.fare.fare_type
    seats = f"{count_of(booking.passengers, f'{fare_type} seat')} to {booking.destination_name}"
    if booking.is_active:
        return [
            f"Booking confirmed: {seats} for {booking.total_price:.2f} EUR.",
            f"Your reference is {booking.reference}. Keep it to view or cancel your booking.",
        ]
    lines = [f"Booking {booking.reference} is cancelled: {seats}."]
    if booking.refund_amount:
        lines.append(
            f"Refund: {booking.refund_amount:.2f} EUR "
            f"({booking.fare.refund_percent:g}% of the {booking.total_price:.2f} EUR paid)."
        )
    else:
        lines.append(f"No refund, because this {fare_type} ticket is not refundable.")
    return lines


def format_confirmation(booking: Booking) -> list[str]:
    """Return the booking confirmation."""
    fare = booking.fare
    arrival_format = "%H:%M" if booking.arrival.date() == booking.departure.date() else DAY_FORMAT
    change = f"yes, fee {fare.change_fee:.2f} EUR" if fare.changeable else "no"
    refund = f"yes, {fare.refund_percent:g}% of the price" if fare.refundable else "no"
    lines = [
        RULE,
        f"BOOKING {booking.status.upper()}",
        RULE,
        *format_outcome(booking),
        "",
        f"Reference:      {booking.reference}",
        f"Journey:        Madrid to {booking.destination_name} with {booking.company_name}",
        f"Departure:      {booking.departure:%a %d %b %Y %H:%M}",
        f"Arrival:        {booking.arrival:{arrival_format}}",
        f"Offer:          {booking.offer_id} ({fare.fare_type} fare)",
        f"Passengers:     {booking.passengers}",
        f"Total price:    {booking.total_price:.2f} EUR ({fare.price:.2f} EUR per person)",
        f"Payment:        {booking.payment_status}",
        "Fare conditions stored with this booking:",
        f"  changeable:   {change}",
        f"  refundable:   {refund}",
        f"  luggage:      {fare.luggage_kg} kg per person",
        f"Booked at:      {booking.booked_at:%Y-%m-%d %H:%M}",
    ]
    lines.append("No real payment was taken. This is a simulation.")
    return lines


def format_bookings(bookings: Sequence[Booking]) -> list[str]:
    """Return the saved bookings as a table."""
    if not bookings:
        return ["You have no bookings yet. Search for a bus, then use the book command."]
    lines = [
        f"{'Reference':<20}{'Offer':<15}{'Departure':<18}{'Pass.':>5} {'Total':>10}  "
        f"{'Status':<10}{'Refund':>10}"
    ]
    for booking in bookings:
        refund = "" if booking.refund_amount is None else f"{booking.refund_amount:.2f} EUR"
        lines.append(
            f"{booking.reference:<20}{booking.offer_id:<15}{booking.departure:{DAY_FORMAT}}  "
            f"{booking.passengers:>5} {booking.total_price:>6.2f} EUR  "
            f"{booking.status:<10}{refund:>10}".rstrip()
        )
    active = sum(1 for booking in bookings if booking.is_active)
    lines += ["", f"{count_of(len(bookings), 'booking')}: {active} confirmed, {len(bookings) - active} cancelled."]
    if active:
        lines.append("To cancel a confirmed booking, use the cancel command with its reference.")
    return lines
