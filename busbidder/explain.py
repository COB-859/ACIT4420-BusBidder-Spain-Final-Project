"""Builds the short explanation of why the top offer ranks first.

The text is generated from the scores, so it always matches the table
above it. It names advantages and a remaining disadvantage, and never
calls the offer objectively best.
"""

from __future__ import annotations

from collections.abc import Sequence

from busbidder.criteria import CRITERIA
from busbidder.ranking import ScoredOffer

MAX_ADVANTAGES = 2
DISADVANTAGE_BELOW_POINTS = 60.0

CLOSING = (
    "This result holds only for the mandatory conditions, weights and scoring rules "
    "used here. With other weights, another offer can rank first."
)


def explain_top(ranked: Sequence[ScoredOffer]) -> list[str]:
    """Return the explanation for the first offer of a ranking, as lines of text."""
    if not ranked:
        return []
    top = ranked[0]
    offer = top.offer
    lines = [
        f"{offer.offer_id} ({offer.company.name}, {offer.fare.fare_type} fare) "
        f"ranks first with {top.total:.2f} points out of 100."
    ]
    if len(ranked) == 1:
        lines.append(
            "It is the only offer that meets the mandatory conditions, so there is nothing "
            "to compare it with. Its points for criteria that compare offers with each "
            "other (such as price and duration) say only that no other offer was left."
        )
    else:
        lines.extend(_compare_with_runner_up(top, ranked[1]))
    lines.append(_weakest_point(top))
    if any(score.estimated for score in top.scores.values()):
        lines.append(
            "Its emissions value is not reported, so its emissions points are an estimate "
            "(the median of the offers that report a value)."
        )
    lines.append(CLOSING)
    return lines


def _label(name: str) -> str:
    """Return a criterion's label in lower case."""
    return CRITERIA[name].label.lower()


def _compare_with_runner_up(top: ScoredOffer, runner_up: ScoredOffer) -> list[str]:
    """Say where the top offer gains and loses against the second offer."""
    gains = {
        name: top.contributions[name] - runner_up.contributions[name] for name in top.contributions
    }
    # Largest gain first. sorted() keeps the registry order for equal gains.
    by_gain = sorted(gains, key=lambda name: -gains[name])
    advantages = [name for name in by_gain if gains[name] > 0][:MAX_ADVANTAGES]
    lines = []
    runner_text = f"the runner-up {runner_up.offer.offer_id} ({runner_up.total:.2f} points)"
    if top.total == runner_up.total:
        lines.append(
            f"It has the same total as {runner_text} and is placed first by the tie rule: "
            "lower price, then earlier departure, then offer ID."
        )
    if advantages:
        parts = [
            f"{_label(name)} ({top.scores[name].value} against {runner_up.scores[name].value}, "
            f"+{gains[name]:.2f} points)"
            for name in advantages
        ]
        lines.append(f"Compared with {runner_text}, it gains most on " + " and ".join(parts) + ".")
    elif top.total != runner_up.total:
        lines.append(f"It is ahead of {runner_text}.")
    worst = by_gain[-1]
    if gains[worst] < 0:
        lines.append(
            f"The runner-up is better on {_label(worst)} "
            f"({runner_up.scores[worst].value} against {top.scores[worst].value}, "
            f"{gains[worst]:.2f} points for the top offer)."
        )
    return lines


def _weakest_point(top: ScoredOffer) -> str:
    """Name the criterion with the lowest points, if it is clearly weak."""
    # min() returns the first criterion in registry order when points are equal.
    weakest = min(top.scores, key=lambda name: top.scores[name].points)
    score = top.scores[weakest]
    if score.points < DISADVANTAGE_BELOW_POINTS:
        return (
            f"Remaining disadvantage: {_label(weakest)} "
            f"({score.value} scores {score.points:.1f} of 100)."
        )
    return (
        f"No scored criterion is below {DISADVANTAGE_BELOW_POINTS:.0f} points, "
        "so there is no clear disadvantage among the scored criteria."
    )
