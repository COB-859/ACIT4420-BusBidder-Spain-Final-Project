"""Hard constraints: the mandatory conditions an offer must meet.

Each constraint is a small function that registers itself with the
@constraint decorator. The filter walks through the registry, so adding a
constraint means adding one function and changing nothing else.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from busbidder.models import Offer, TravelRequest, format_duration

# A constraint gets the offer, the request and the seats left on the
# offer's departure. It returns None when the offer passes, or the reason
# it fails.
ConstraintFunction = Callable[[Offer, TravelRequest, int], "str | None"]

CONSTRAINTS: dict[str, ConstraintFunction] = {}


def constraint(name: str) -> Callable[[ConstraintFunction], ConstraintFunction]:
    """Register the decorated function as the hard constraint called name."""

    def register(function: ConstraintFunction) -> ConstraintFunction:
        if name in CONSTRAINTS:
            raise ValueError(f"constraint {name!r} is registered twice")
        CONSTRAINTS[name] = function
        return function

    return register


@constraint("seats")
def enough_seats(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The departure must have a seat for every passenger."""
    if seats_left >= request.passengers:
        return None
    if seats_left == 0:
        return "sold out"
    return f"{seats_left} seat(s) left, {request.passengers} needed"


@constraint("budget")
def within_budget(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The price per person must not be above the budget."""
    if request.budget is None or offer.price <= request.budget:
        return None
    return f"price {offer.price:.2f} EUR is above the budget of {request.budget:.2f} EUR"


@constraint("transfers")
def few_enough_transfers(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The journey must not have more transfers than allowed."""
    if request.max_transfers is None or offer.departure.transfers <= request.max_transfers:
        return None
    return f"{offer.departure.transfers} transfer(s), at most {request.max_transfers} allowed"


@constraint("duration")
def short_enough(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The journey must not be longer than the limit."""
    limit = request.max_duration_minutes
    if limit is None or offer.duration_minutes <= limit:
        return None
    return (
        f"journey of {format_duration(offer.duration_minutes)} is longer than "
        f"the limit of {format_duration(limit)}"
    )


@constraint("changeable")
def changeable_if_required(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The ticket must be changeable when the traveller requires it."""
    if not request.require_changeable or offer.fare.changeable:
        return None
    return "ticket cannot be changed"


@constraint("refundable")
def refundable_if_required(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The ticket must be refundable when the traveller requires it."""
    if not request.require_refundable or offer.fare.refundable:
        return None
    return "ticket cannot be refunded"


@constraint("luggage")
def enough_luggage(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The luggage allowance must cover what the traveller needs."""
    if request.min_luggage_kg is None or offer.fare.luggage_kg >= request.min_luggage_kg:
        return None
    return f"luggage allowance of {offer.fare.luggage_kg} kg is below the required {request.min_luggage_kg} kg"


def describe_constraints(request: TravelRequest) -> list[str]:
    """Return the mandatory conditions of a request as text for the user."""
    lines = [f"at least {request.passengers} seat(s) left"]
    if request.budget is not None:
        lines.append(f"price per person at most {request.budget:.2f} EUR")
    if request.max_transfers is not None:
        lines.append(f"at most {request.max_transfers} transfer(s)")
    if request.max_duration_minutes is not None:
        lines.append(f"journey at most {format_duration(request.max_duration_minutes)}")
    if request.require_changeable:
        lines.append("ticket must be changeable")
    if request.require_refundable:
        lines.append("ticket must be refundable")
    if request.min_luggage_kg is not None:
        lines.append(f"luggage allowance at least {request.min_luggage_kg} kg")
    return lines


def in_scope(offer: Offer, request: TravelRequest) -> bool:
    """Return True when the offer is for the requested city and travel dates.

    Destination and dates decide which offers are looked at. They are not
    counted as removals, because every search would otherwise report that
    the destination removed nearly all offers.
    """
    return (
        offer.departure.destination.code == request.destination.code
        and request.date_from <= offer.departure.departure.date() <= request.date_to
    )


@dataclass(frozen=True)
class RemovedOffer:
    """An offer that failed at least one constraint, with every reason."""

    offer: Offer
    reasons: dict[str, str]


@dataclass
class FilterResult:
    """What the filter did: the offers kept and the offers removed."""

    in_scope: int = 0
    accepted: list[Offer] = field(default_factory=list)
    removed: list[RemovedOffer] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def strictest(self) -> str | None:
        """Return the constraint that removed the most offers, if any did."""
        name = max(self.counts, key=self.counts.get, default=None)
        if name is None or self.counts[name] == 0:
            return None
        return name


def filter_offers(
    offers: Iterable[Offer],
    request: TravelRequest,
    seats_left: Mapping[str, int],
) -> FilterResult:
    """Keep the offers in scope that pass every hard constraint.

    Every offer is tested against every constraint, so the count for a
    constraint does not depend on the order of the checks.
    """
    result = FilterResult(counts={name: 0 for name in CONSTRAINTS})
    for offer in offers:
        if not in_scope(offer, request):
            continue
        result.in_scope += 1
        left = seats_left.get(offer.departure.departure_id, 0)
        reasons = {}
        for name, check in CONSTRAINTS.items():
            reason = check(offer, request, left)
            if reason is not None:
                reasons[name] = reason
                result.counts[name] += 1
        if reasons:
            result.removed.append(RemovedOffer(offer, reasons))
        else:
            result.accepted.append(offer)
    return result
