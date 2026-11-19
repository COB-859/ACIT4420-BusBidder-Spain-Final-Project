"""Hard constraints: the mandatory conditions an offer must meet.

Each constraint is a small function that registers itself, together with
the text that describes it to the traveller, through the @constraint
decorator. The filter and the list of mandatory conditions both walk
through the registry, so neither changes when a constraint is added. A
constraint that needs a new value from the traveller also needs a field
in TravelRequest and a command-line option.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from busbidder.models import Offer, TravelRequest, count_of, format_duration

# The check gets the offer, the request and the seats left on the offer's
# departure. It returns None when the offer passes, or the reason it fails.
CheckFunction = Callable[[Offer, TravelRequest, int], "str | None"]
# The description says what the traveller asked for, or None when the
# traveller did not set this condition.
DescribeFunction = Callable[[TravelRequest], "str | None"]
# Given the offers that fail only this constraint, and the words " that
# meets your other requirements" or nothing, the suggestion names the
# value that would work, such as the cheapest price.
SuggestFunction = Callable[[Sequence["RemovedOffer"], str], str]


@dataclass(frozen=True)
class Constraint:
    """A registered hard constraint: its name, its check and its texts.

    problem and suggest are used when no offer is left. problem says what
    is wrong with the offers, in the traveller's words ("cost more than
    your budget of 20.00 EUR"). suggest names the value that would work.
    """

    name: str
    check: CheckFunction
    describe: DescribeFunction
    problem: DescribeFunction | None = None
    suggest: SuggestFunction | None = None


CONSTRAINTS: dict[str, Constraint] = {}


def constraint(
    name: str,
    describe: DescribeFunction,
    problem: DescribeFunction | None = None,
    suggest: SuggestFunction | None = None,
) -> Callable[[CheckFunction], CheckFunction]:
    """Register the decorated function as the hard constraint called name.

    describe turns a request into the text shown to the traveller, so a
    constraint that filters offers is always also listed as a mandatory
    condition. problem and suggest give the advice shown when no offer is
    left.
    """

    def register(function: CheckFunction) -> CheckFunction:
        if name in CONSTRAINTS:
            raise ValueError(f"constraint {name!r} is registered twice")
        CONSTRAINTS[name] = Constraint(name, function, describe, problem, suggest)
        return function

    return register


@constraint(
    "seats",
    describe=lambda request: f"at least {count_of(request.passengers, 'seat')} left",
    problem=lambda request: "are sold out" if request.passengers == 1
    else f"have fewer than {request.passengers} seats left",
    suggest=lambda alone, others: (
        f"The most seats left on one bus{others} is {max(entry.seats_left for entry in alone)}."
    ),
)
def enough_seats(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The departure must have a seat for every passenger."""
    if seats_left >= request.passengers:
        return None
    if seats_left == 0:
        return "sold out"
    return f"{count_of(seats_left, 'seat')} left, {request.passengers} needed"


@constraint(
    "budget",
    describe=lambda request: None if request.budget is None
    else f"price per person at most {request.budget:.2f} EUR",
    problem=lambda request: f"cost more than your budget of {request.budget:.2f} EUR",
    suggest=lambda alone, others: (
        f"The cheapest offer{others} costs {min(entry.offer.price for entry in alone):.2f} EUR."
    ),
)
def within_budget(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The price per person must not be above the budget."""
    if request.budget is None or offer.price <= request.budget:
        return None
    return f"price {offer.price:.2f} EUR is above the budget of {request.budget:.2f} EUR"


@constraint(
    "transfers",
    describe=lambda request: None if request.max_transfers is None
    else f"at most {count_of(request.max_transfers, 'transfer')}",
    problem=lambda request: f"have more than {count_of(request.max_transfers, 'transfer')}",
    suggest=lambda alone, others: (
        f"The offer with the fewest transfers{others} has "
        f"{count_of(min(entry.offer.departure.transfers for entry in alone), 'transfer')}."
    ),
)
def few_enough_transfers(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The journey must not have more transfers than allowed."""
    if request.max_transfers is None or offer.departure.transfers <= request.max_transfers:
        return None
    return f"{count_of(offer.departure.transfers, 'transfer')}, at most {request.max_transfers} allowed"


@constraint(
    "duration",
    describe=lambda request: None if request.max_duration_minutes is None
    else f"journey at most {format_duration(request.max_duration_minutes)}",
    problem=lambda request: f"take longer than {format_duration(request.max_duration_minutes)}",
    suggest=lambda alone, others: (
        f"The shortest journey{others} takes "
        f"{format_duration(min(entry.offer.duration_minutes for entry in alone))}."
    ),
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
    problem=lambda request: "cannot be changed",
    suggest=lambda alone, others: (
        f"Without this requirement, {count_of(len(alone), 'offer')} would match."
    ),
)
def changeable_if_required(offer: Offer, request: TravelRequest, seats_left: int) -> str | None:
    """The ticket must be changeable when the traveller requires it."""
    if not request.require_changeable or offer.fare.changeable:
        return None
    return "ticket cannot be changed"


@constraint(
    "refundable",
    describe=lambda request: "ticket must be refundable" if request.require_refundable else None,
    problem=lambda request: "cannot be refunded",
    suggest=lambda alone, others: (
        f"Without this requirement, {count_of(len(alone), 'offer')} would match."
    ),
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
    problem=lambda request: f"allow less than {request.min_luggage_kg} kg of luggage",
    suggest=lambda alone, others: (
        f"The highest luggage allowance{others} is "
        f"{max(entry.offer.fare.luggage_kg for entry in alone)} kg."
    ),
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
    seats_left: int = 0


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
            result.removed.append(RemovedOffer(offer, reasons, left))
        else:
            result.accepted.append(offer)
    return result


def advise(result: FilterResult, request: TravelRequest) -> list[str]:
    """Say why no offer is left and which value would work.

    The advice is about a constraint the traveller can act on: one that
    some offers fail and nothing else. Changing that constraint alone
    gives those offers. Among such constraints the one that removed the
    most offers is used, and the first registered one when two removed
    equally many. The suggestion is worked out from the offers that fail
    only that constraint. The cheapest offer of all is the wrong answer
    when it also fails another requirement.

    When every offer fails two requirements or more, no single change is
    enough, and the advice says so.
    """
    strictest = result.strictest()
    if strictest is None:
        return []
    fails_only: dict[str, list[RemovedOffer]] = {}
    for entry in result.removed:
        if len(entry.reasons) == 1:
            fails_only.setdefault(next(iter(entry.reasons)), []).append(entry)
    name = max(fails_only, key=lambda key: result.counts[key], default=strictest)
    rule = CONSTRAINTS[name]
    problem = (rule.problem(request) if rule.problem is not None else None) or f"fail the condition '{name}'"
    lines = [f"{result.counts[name]} of the {result.in_scope} offers {problem}."]
    alone = fails_only.get(name, [])
    if not alone:
        lines.append(
            "Changing one requirement alone is not enough: every offer fails at least two "
            "of your requirements. Try removing a requirement."
        )
    elif rule.suggest is not None:
        # "at least 1 seat left" is always a condition, so it is only
        # mentioned as another requirement when the traveller asked for more.
        set_by_traveller = {
            other for other, constraint_rule in CONSTRAINTS.items()
            if other != name and constraint_rule.describe(request) is not None
            and not (other == "seats" and request.passengers == 1)
        }
        others = " that meets your other requirements" if set_by_traveller else ""
        lines.append(rule.suggest(alone, others))
    return lines
