"""Hard constraints: the mandatory conditions an offer must meet.

Each constraint is a small function that registers itself, together with
the text that describes it to the traveller, through the @constraint
decorator. The filter and the list of mandatory conditions both walk
through the registry, so neither changes when a constraint is added. A
constraint that needs a new value from the traveller also needs a field
in TravelRequest and a command-line option.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from busbidder.models import Offer, TravelRequest, format_duration

# The check gets the offer, the request and the seats left on the offer's
# departure. It returns None when the offer passes, or the reason it fails.
CheckFunction = Callable[[Offer, TravelRequest, int], "str | None"]
# The description says what the traveller asked for, or None when the
# traveller did not set this condition.
DescribeFunction = Callable[[TravelRequest], "str | None"]


@dataclass(frozen=True)
class Constraint:
    """A registered hard constraint: its name, its check and its description."""

    name: str
    check: CheckFunction
    describe: DescribeFunction


CONSTRAINTS: dict[str, Constraint] = {}


def constraint(name: str, describe: DescribeFunction) -> Callable[[CheckFunction], CheckFunction]:
    """Register the decorated function as the hard constraint called name.

    describe turns a request into the text shown to the traveller, so a
    constraint that filters offers is always also listed as a mandatory
    condition.
    """

    def register(function: CheckFunction) -> CheckFunction:
        if name in CONSTRAINTS:
            raise ValueError(f"constraint {name!r} is registered twice")
        CONSTRAINTS[name] = Constraint(name, function, describe)
        return function

    return register


@constraint("seats", describe=lambda request: f"at least {request.passengers} seat(s) left")
def enough_seats(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The departure must have a seat for every passenger."""
    if seats_left >= request.passengers:
        return None
    if seats_left == 0:
        return "sold out"
    return f"{seats_left} seat(s) left, {request.passengers} needed"


@constraint(
    "budget",
    describe=lambda request: None if request.budget is None
    else f"price per person at most {request.budget:.2f} EUR",
)
def within_budget(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The price per person must not be above the budget."""
    if request.budget is None or offer.price <= request.budget:
        return None
    return f"price {offer.price:.2f} EUR is above the budget of {request.budget:.2f} EUR"


@constraint(
    "transfers",
    describe=lambda request: None if request.max_transfers is None
    else f"at most {request.max_transfers} transfer(s)",
)
def few_enough_transfers(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The journey must not have more transfers than allowed."""
    if request.max_transfers is None or offer.departure.transfers <= request.max_transfers:
        return None
    return f"{offer.departure.transfers} transfer(s), at most {request.max_transfers} allowed"


@constraint(
    "duration",
    describe=lambda request: None if request.max_duration_minutes is None
    else f"journey at most {format_duration(request.max_duration_minutes)}",
)
def short_enough(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The journey must not be longer than the limit."""
    limit = request.max_duration_minutes
    if limit is None or offer.duration_minutes <= limit:
        return None
    return (
        f"journey of {format_duration(offer.duration_minutes)} is longer than "
        f"the limit of {format_duration(limit)}"
    )


@constraint(
    "changeable",
    describe=lambda request: "ticket must be changeable" if request.require_changeable else None,
)
def changeable_if_required(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The ticket must be changeable when the traveller requires it."""
    if not request.require_changeable or offer.fare.changeable:
        return None
    return "ticket cannot be changed"


@constraint(
    "refundable",
    describe=lambda request: "ticket must be refundable" if request.require_refundable else None,
)
def refundable_if_required(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The ticket must be refundable when the traveller requires it."""
    if not request.require_refundable or offer.fare.refundable:
        return None
    return "ticket cannot be refunded"


@constraint(
    "luggage",
    describe=lambda request: None if request.min_luggage_kg is None
    else f"luggage allowance at least {request.min_luggage_kg} kg",
)
def enough_luggage(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The luggage allowance must cover what the traveller needs."""
    if request.min_luggage_kg is None or offer.fare.luggage_kg >= request.min_luggage_kg:
        return None
    return f"luggage allowance of {offer.fare.luggage_kg} kg is below the required {request.min_luggage_kg} kg"


def describe_constraints(request: TravelRequest) -> list[str]:
    """Return the mandatory conditions of a request as text for the user."""
    descriptions = (rule.describe(request) for rule in CONSTRAINTS.values())
    return [text for text in descriptions if text is not None]


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
        for name, rule in CONSTRAINTS.items():
            reason = rule.check(offer, request, left)
            if reason is not None:
                reasons[name] = reason
                result.counts[name] += 1
        if reasons:
            result.removed.append(RemovedOffer(offer, reasons))
        else:
            result.accepted.append(offer)
    return result
