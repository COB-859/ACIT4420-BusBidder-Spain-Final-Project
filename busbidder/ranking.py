"""Weighted ranking: checks the weights and puts the offers in order.

Total score = sum over the criteria of (weight / 100) x points, so the
total is also on a scale from 0 to 100.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real

from busbidder.criteria import CORE_CRITERIA, CRITERIA, Score
from busbidder.exceptions import WeightError
from busbidder.models import Offer, TravelRequest

PERCENT_TOTAL = 100.0
PERCENT_TOLERANCE = 0.01
FRACTION_TOTAL = 1.0
FRACTION_TOLERANCE = 0.0001
DOMINANT_WEIGHT_PERCENT = 70.0
SCORE_DECIMALS = 2


def parse_weights(text: str) -> dict[str, float]:
    """Turn text such as 'price=40,duration=25,time=15,flex=20' into weights."""
    weights: dict[str, float] = {}
    for part in text.split(","):
        name, separator, number = part.partition("=")
        name = name.strip().lower()
        if not separator or not name:
            raise WeightError(f"weights must be written as name=number, separated by commas: {part.strip()!r}")
        if name in weights:
            raise WeightError(f"weight given twice: {name}")
        try:
            # "40%" means the same as "40": weights are percentages.
            weights[name] = float(number.strip().removesuffix("%"))
        except ValueError:
            raise WeightError(f"weight for {name} is not a number: {number.strip()!r}") from None
    return weights


def check_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Return the weights as percentages, or raise WeightError.

    Accepted: percentages that total 100, or fractions that total 1.0.
    Nothing is rescaled silently. Criteria with weight 0 are left out of
    the result, so they are neither calculated nor shown.
    """
    unknown = [name for name in weights if name not in CRITERIA]
    if unknown:
        raise WeightError(
            f"unknown criterion: {', '.join(unknown)}. Known criteria: {', '.join(CRITERIA)}"
        )
    for name, weight in weights.items():
        if isinstance(weight, bool) or not isinstance(weight, Real) or not _is_finite(weight):
            raise WeightError(f"weight for {name} is not a usable number: {weight!r}")
        if weight < 0:
            raise WeightError(f"weight for {name} is negative: {weight:g}")
    total = sum(weights.values())
    if abs(total - PERCENT_TOTAL) <= PERCENT_TOLERANCE:
        scale = 1.0
    elif abs(total - FRACTION_TOTAL) <= FRACTION_TOLERANCE:
        scale = PERCENT_TOTAL
    else:
        raise WeightError(f"weights must total 100 (or 1.0), but they total {total:g}")
    missing = [name for name in CORE_CRITERIA if weights.get(name, 0) <= 0]
    if missing:
        raise WeightError(
            "the ranking must use price, duration, time and flex; "
            f"no weight above 0 for: {', '.join(missing)}"
        )
    return {name: weights[name] * scale for name in CRITERIA if weights.get(name, 0) > 0}


def _is_finite(number: Real) -> bool:
    """Return True for a finite number. A huge whole number counts as not finite."""
    try:
        return math.isfinite(number)
    except OverflowError:
        return False


def dominant_criterion(weights: Mapping[str, float]) -> str | None:
    """Return the criterion whose weight is above the dominance limit, if there is one."""
    for name, weight in weights.items():
        if weight > DOMINANT_WEIGHT_PERCENT:
            return name
    return None


def describe_dominance(weights: Mapping[str, float]) -> str | None:
    """Say which criterion would decide most of the ranking, or return None."""
    name = dominant_criterion(weights)
    if name is None:
        return None
    return (
        f"{CRITERIA[name].label} has a weight of {weights[name]:g}%, above "
        f"{DOMINANT_WEIGHT_PERCENT:g}%, so it would decide most of the ranking"
    )


def require_intended_dominance(weights: Mapping[str, float], intended: bool) -> None:
    """Refuse weights that let one criterion dominate, unless that is intended.

    This stops one criterion from dominating by accident, for example
    through a typing error, while a traveller who really cares about one
    thing above all can still say so.
    """
    problem = describe_dominance(weights)
    if problem is not None and not intended:
        raise WeightError(f"{problem}. If that is intended, add --allow-dominant")


# When the first two offers are closer than this, the traveller is told
# that the recommendation is not stable.
NEAR_TIE_POINTS = 1.0


def near_tie_gap(ranked: Sequence[ScoredOffer]) -> float | None:
    """Return the gap between the first two totals when it is above 0 and below the limit."""
    if len(ranked) < 2:
        return None
    gap = round(ranked[0].total - ranked[1].total, 2)
    return gap if 0 < gap < NEAR_TIE_POINTS else None


@dataclass(frozen=True)
class ScoredOffer:
    """An offer with its points and contribution per criterion, and its total."""

    offer: Offer
    scores: dict[str, Score]
    contributions: dict[str, float]
    total: float


def rank_offers(
    offers: Sequence[Offer],
    request: TravelRequest,
    weights: Mapping[str, float],
) -> list[ScoredOffer]:
    """Score the offers and return them best first.

    Ties on the rounded total are broken by lower price, then earlier
    departure, then offer ID, so the order is the same on every run.
    """
    percentages = check_weights(weights)
    offers = list(offers)
    columns = {name: CRITERIA[name].function(offers, request) for name in percentages}
    ranked = []
    for index, offer in enumerate(offers):
        scores = {name: columns[name][index] for name in percentages}
        contributions = {
            name: percentages[name] / PERCENT_TOTAL * scores[name].points for name in percentages
        }
        total = round(sum(contributions.values()), SCORE_DECIMALS)
        ranked.append(ScoredOffer(offer, scores, contributions, total))
    ranked.sort(key=lambda scored: (
        -scored.total,
        scored.offer.price,
        scored.offer.departure.departure,
        scored.offer.offer_id,
    ))
    return ranked
