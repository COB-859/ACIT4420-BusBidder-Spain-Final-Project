"""Builds the text the user sees: request summary, ranking, comparison.

Every function returns lines of text and prints nothing, so the output
can be tested and saved to a file as well as shown on screen.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from busbidder.constraints import FilterResult, describe_constraints
from busbidder.criteria import CRITERIA
from busbidder.explain import explain_top
from busbidder.models import Departure, TravelRequest, format_duration
from busbidder.ranking import ScoredOffer, dominant_criterion

DAY_FORMAT = "%a %d %b %H:%M"
RULE = "-" * 72


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
        f"{request.date_from} to {request.date_to}, {request.passengers} passenger(s)",
        "",
        "Mandatory conditions (an offer that fails one is removed before ranking):",
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
    dominant = dominant_criterion(weights)
    if dominant is not None:
        lines.append(
            f"  Note: {CRITERIA[dominant].label} has a weight above 70%, "
            "so it will decide most of the ranking."
        )
    return lines


def format_filter(result: FilterResult) -> list[str]:
    """Return what the filter kept and removed."""
    lines = ["", f"Offers for this city and these dates: {result.in_scope}"]
    if result.in_scope == 0:
        lines.append("There is nothing to rank. Try other dates.")
        return lines
    lines.append(f"Removed by a mandatory condition: {len(result.removed)}")
    used = [f"{name} {count}" for name, count in result.counts.items() if count]
    if used:
        lines.append(f"  removed per condition (an offer can fail several): {', '.join(used)}")
    lines.append(f"Offers left to rank: {len(result.accepted)}")
    if not result.accepted:
        strictest = result.strictest()
        lines.append(
            f"No offer meets every mandatory condition. The condition that removed the "
            f"most offers is '{strictest}' ({result.counts[strictest]}). Consider relaxing it."
        )
    return lines


def format_removed(result: FilterResult) -> list[str]:
    """Return every removed offer with all the reasons it was removed."""
    lines = ["", "Removed offers and the reasons:"]
    for removed in result.removed:
        reasons = "; ".join(removed.reasons.values())
        lines.append(f"  {removed.offer.offer_id}: {reasons}")
    return lines


def format_scored_offer(rank: int, scored: ScoredOffer, weights: Mapping[str, float],
                        seats_left: int) -> list[str]:
    """Return one ranked offer: its values, points, contributions and total."""
    offer = scored.offer
    transfers = offer.departure.transfers
    route = "direct" if transfers == 0 else f"{transfers} transfer(s)"
    lines = [
        f"#{rank}  {offer.offer_id}  {offer.company.name}, {offer.fare.fare_type} fare"
        f"   TOTAL {scored.total:.2f}",
        f"    {format_when(offer.departure)} ({format_duration(offer.duration_minutes)}), "
        f"{route}, {seats_left} seat(s) left, luggage {offer.fare.luggage_kg} kg",
    ]
    for name, score in scored.scores.items():
        value = score.value + (" (estimated)" if score.estimated else "")
        lines.append(
            f"    {CRITERIA[name].label:<12}{value:<26}{score.points:>6.1f} pts"
            f" x {weights[name]:>5g}% = {scored.contributions[name]:>6.2f}"
        )
    return lines


def format_ranking(ranked: Sequence[ScoredOffer], weights: Mapping[str, float],
                   seats_left: Mapping[str, int], top: int) -> list[str]:
    """Return the ranked offers, best first, followed by the explanation."""
    if not ranked:
        return []
    shown = ranked if top <= 0 else ranked[:top]
    lines = ["", RULE, "Ranked offers, best first (value, points, weight, contribution)", RULE]
    for rank, scored in enumerate(shown, start=1):
        left = seats_left.get(scored.offer.departure.departure_id, 0)
        lines.extend(format_scored_offer(rank, scored, weights, left))
        lines.append("")
    if len(shown) < len(ranked):
        lines.append(f"Showing {len(shown)} of {len(ranked)} ranked offers. Use --top 0 to show all.")
        lines.append("")
    lines.append("Why #1 ranks first:")
    lines.extend(f"  {line}" for line in explain_top(ranked))
    return lines


def format_comparison(
    results: Mapping[str, tuple[str, Mapping[str, float], Sequence[ScoredOffer]]],
    seats_left: Mapping[str, int],
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
            f"The {len(results)} profiles recommend {len(winners)} different offer(s)."
        )
    for key, (description, weights, ranked) in results.items():
        lines.extend(["", RULE, f"Profile '{key}': {description}", f"Weights: {format_weights(weights)}", RULE])
        for rank, scored in enumerate(ranked[:1], start=1):
            left = seats_left.get(scored.offer.departure.departure_id, 0)
            lines.extend(format_scored_offer(rank, scored, weights, left))
        runners = [f"#{rank} {scored.offer.offer_id} ({scored.total:.2f})"
                   for rank, scored in enumerate(ranked[1:3], start=2)]
        if runners:
            lines.append(f"    next: {', '.join(runners)}")
        if ranked:
            lines.append("")
            lines.extend(f"  {line}" for line in explain_top(ranked))
    return lines
