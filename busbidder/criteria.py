"""Scoring criteria: the soft preferences an offer earns points for.

Every criterion turns a value in its own unit (euros, minutes, a time of
day) into points from 0 to 100, where 100 is best, so the values can be
weighted and added. Each criterion registers itself with the @criterion
decorator; the ranker walks through the registry.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from busbidder.models import Offer, TravelRequest, count_of, format_duration
from busbidder.validation import RATING_MAX, RATING_MIN

MAX_POINTS = 100.0
NEUTRAL_POINTS = 50.0

# A range smaller than this is stretched no further, so a trivial
# difference (1 euro, 5 minutes) is not turned into 0 against 100.
MIN_PRICE_RANGE_EUR = 5.0
MIN_DURATION_RANGE_MINUTES = 30.0
MIN_EMISSIONS_RANGE_KG = 1.0

# Departure convenience reaches 0 points this far outside the preferred band.
TIME_ZERO_AT_MINUTES = 360

POINTS_CHANGEABLE = 50.0
POINTS_REFUNDABLE = 50.0
TRANSFER_POINTS = {0: 100.0, 1: 50.0}

CORE_CRITERIA = ("price", "duration", "time", "flex")


@dataclass(frozen=True)
class Score:
    """The result of one criterion for one offer."""

    points: float
    value: str
    estimated: bool = False


# A criterion gets all offers that passed the filter, because some scores
# are relative to the other offers. It returns one Score per offer.
CriterionFunction = Callable[[Sequence[Offer], TravelRequest], "list[Score]"]


@dataclass(frozen=True)
class Criterion:
    """A registered criterion: its name, its label for the user and its function."""

    name: str
    label: str
    function: CriterionFunction


CRITERIA: dict[str, Criterion] = {}


def criterion(name: str, label: str) -> Callable[[CriterionFunction], CriterionFunction]:
    """Register the decorated function as the scoring criterion called name."""

    def register(function: CriterionFunction) -> CriterionFunction:
        if name in CRITERIA:
            raise ValueError(f"criterion {name!r} is registered twice")
        CRITERIA[name] = Criterion(name, label, function)
        return function

    return register


def relative_points(values: Sequence[float], minimum_range: float) -> list[float]:
    """Return points for values where lower is better, relative to each other.

    The lowest value gets 100. Points fall in a straight line to 0 at the
    highest value, or at lowest + minimum_range when the real range is
    smaller than that.
    """
    if not values:
        return []
    best = min(values)
    span = max(max(values) - best, minimum_range)
    return [MAX_POINTS * (1 - (value - best) / span) for value in values]


@criterion("price", "Price")
def price_score(offers: Sequence[Offer], request: TravelRequest) -> list[Score]:
    """Cheaper is better, compared with the other offers."""
    points = relative_points([offer.price for offer in offers], MIN_PRICE_RANGE_EUR)
    return [Score(point, f"{offer.price:.2f} EUR") for point, offer in zip(points, offers)]


@criterion("duration", "Duration")
def duration_score(offers: Sequence[Offer], request: TravelRequest) -> list[Score]:
    """Shorter is better, compared with the other offers."""
    points = relative_points(
        [offer.duration_minutes for offer in offers], MIN_DURATION_RANGE_MINUTES
    )
    return [
        Score(point, format_duration(offer.duration_minutes))
        for point, offer in zip(points, offers)
    ]


@criterion("time", "Departure")
def departure_time_score(offers: Sequence[Offer], request: TravelRequest) -> list[Score]:
    """Full points inside the preferred band, falling to 0 six hours outside it."""
    scores = []
    for offer in offers:
        clock = offer.departure.departure.time()
        outside = request.preferred_departure.minutes_outside(clock)
        points = max(0.0, MAX_POINTS * (1 - outside / TIME_ZERO_AT_MINUTES))
        scores.append(Score(points, f"{clock:%H:%M}"))
    return scores


@criterion("flex", "Flexibility")
def flexibility_score(offers: Sequence[Offer], request: TravelRequest) -> list[Score]:
    """Half the points for a changeable ticket, half for a refundable one."""
    scores = []
    for offer in offers:
        fare = offer.fare
        points = POINTS_CHANGEABLE * fare.changeable + POINTS_REFUNDABLE * fare.refundable
        words = [word for word, has in (("changeable", fare.changeable),
                                        ("refundable", fare.refundable)) if has]
        scores.append(Score(points, " + ".join(words) or "fixed"))
    return scores


@criterion("transfers", "Transfers")
def transfers_score(offers: Sequence[Offer], request: TravelRequest) -> list[Score]:
    """A direct bus gets 100, one transfer 50, more than one 0."""
    scores = []
    for offer in offers:
        transfers = offer.departure.transfers
        value = "direct" if transfers == 0 else count_of(transfers, "transfer")
        scores.append(Score(TRANSFER_POINTS.get(transfers, 0.0), value))
    return scores


@criterion("rating", "Rating")
def rating_score(offers: Sequence[Offer], request: TravelRequest) -> list[Score]:
    """The company's customer rating on a fixed scale from 1 to 5."""
    scores = []
    for offer in offers:
        rating = offer.company.rating
        points = (rating - RATING_MIN) / (RATING_MAX - RATING_MIN) * MAX_POINTS
        scores.append(Score(points, f"{rating:.1f} of 5"))
    return scores


@criterion("emissions", "Emissions")
def emissions_score(offers: Sequence[Offer], request: TravelRequest) -> list[Score]:
    """Lower emissions per passenger is better, compared with the other offers.

    An offer with no reported value gets the median points of the offers
    that report one, because a missing number says nothing about the bus.
    If no offer reports a value, every offer gets a neutral 50.
    """
    reported = [offer.departure.emissions_kg for offer in offers
                if offer.departure.emissions_kg is not None]
    reported_points = iter(relative_points(reported, MIN_EMISSIONS_RANGE_KG))
    scores: list[Score | None] = []
    for offer in offers:
        emissions = offer.departure.emissions_kg
        if emissions is None:
            scores.append(None)
        else:
            scores.append(Score(next(reported_points), f"{emissions:.1f} kg"))
    known = [score.points for score in scores if score is not None]
    fallback = statistics.median(known) if known else NEUTRAL_POINTS
    return [
        score if score is not None else Score(fallback, "not reported", estimated=True)
        for score in scores
    ]
