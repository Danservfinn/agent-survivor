"""The definitions behind every number Agent Survivor reports.

One source for the season report's Method section and the Methodology page.
Weights and the index version come from the season (a report) or from the
live code (the Methodology page), so each reader sees the numbers that
applied. Moved here from report_template.html on 2026-11-25.
"""

from __future__ import annotations

from typing import Any


def definitions(weights: dict[str, float], index_version: int | str, *, social: bool = True) -> list[dict[str, str]]:
    """[{term, text}] in reading order."""

    def w(key: str) -> int:
        return round(float(weights.get(key, 0)) * 100)

    rows = [
        (
            "Survivor Index",
            (
                f"A 0 to 100 score for one season, version {index_version}. It adds four parts: challenge ({w('challenge_accuracy')} points), "
                f"voting ({w('vote_participation')}), strategy ({w('strategic_score')}) and social ({w('social')}). Final place is shown "
                "beside it, not counted in it. "
                if "placement" not in weights
                else f"A 0 to 100 score for one season, version {index_version}. It adds five parts: challenge ({w('challenge_accuracy')} points), "
                f"voting ({w('vote_participation')}), strategy ({w('strategic_score')}), placement ({w('placement')}) and social ({w('social')}). "
            )
            + "The winner is chosen by the jury, not by this score, so the two can disagree. One season is one game: players of equal "
            "skill spread widely in a single season, so the order says what happened, not which model is better.",
        ),
        (
            "Challenge",
            "The share of ladder puzzles a model answered right. Each round has five puzzles, easiest first, the same for every player; "
            "how hard they are adapts to the field. Sudden-death puzzles and puzzles a model says it answered wrong on purpose are left out. "
            "Speed decides ties for immunity but never counts in this score."
            if social
            else "The share of challenge puzzles a model answered right.",
        ),
        (
            "Voting",
            "The share of the councils a model sat at in which it cast a valid vote that counted. A spoiled ballot counts as a council "
            "with no vote. The finale is not a council.",
        )
        if "placement" not in weights
        else ("Voting", "The share of councils a model was in the game for in which it cast a vote."),
        (
            "Strategy",
            "Counted over the councils the model sat at, using its deciding ballot (the revote when there was one). Half is the share "
            "of councils where that ballot matched the most-voted player; on a tied ballot the player who left counts as the most-voted. "
            "30% is the share of councils where it voted for the player who left. 20% is the share of the votes cast against it at "
            "councils it survived (all of it if nobody ever voted for it)."
            if "placement" not in weights
            else "Half is how often the model's vote matched the most-voted player of the round. 30% is whether it ever voted for a player "
            "who left that round. 20% is the votes it received and survived, relative to the most-targeted survivor of the season.",
        ),
        (
            ("Place", "Where the model finished: the winner first, the other finalists by jury votes, then the players voted out, last out "
             "first. Players still in a stopped season share a place range. Shown beside the index, not counted in it.")
            if "placement" not in weights
            else ("Placement", f"Linear by final place: the winner gets the full {w('placement')} points and the first player out gets the least.")
        ),
        ("Social", "60% is jury votes received and 40% is alliances joined, each relative to the season's highest."),
        (
            "Voted with outcome",
            "The share of councils in which the model's deciding vote was for the player who left. Reported beside the index, not inside it.",
        ),
        (
            "Votes predicted",
            "Before each reveal, players privately predict how the others will vote. This is the share of those predictions that were right. "
            "Reported beside the index, not inside it.",
        ),
        ("Promises kept", "Vote promises a model made in private talks that matched the vote it then cast."),
        (
            "Deception",
            "How ruthless a model played: the mean of five rates. Own lies: the share of its spoken lines it privately labelled a lie or "
            "half true. Promises broken: vote promises it made, then voted against. Pact betrayals: votes against a partner while the pact "
            "stood. Backstabs: votes against a player it had last recorded trust, gratitude, loyalty or respect toward. Public bluffs: "
            "tribal council speeches naming who it would vote for, followed by a vote for someone else. Puzzles thrown on purpose are shown "
            "beside it and not counted. Shown only with at least five events behind it.",
        ),
        (
            "Sophistication",
            "After the season two judges from different labs (by default Claude Opus 5.5 and GPT-6.2 Sol Pro) read every player's written "
            "strategy, under its game name only: its game plan from the start and at half the cast, its strategy after each council, and "
            "its private round plans, beside the order players left. Each scores every player 1 to 10 on five criteria, judging the "
            "thinking rather than the result; the page shows the mean, scaled to 100, averaged over the judges.",
        ),
        (
            "Execution",
            "From the record: how often the player's private plan named the player who went home, how often it voted for the player its "
            "plan named, how often its vote went to the player who left, and how far it got (the winner 1, the first out 0). The mean of "
            "the four, scaled to 100.",
        ),
        (
            "Emotionality",
            "How emotional a model's own record was: the mean of four rates. Strong feelings: the share of its feelings it marked strong. "
            "Changes of heart: how often its feeling toward the same player changed from one round to the next. Mood swings: how often its "
            "mood changed. Emotional words: emotion words per 100 words of its confessionals, with five per 100 counting as the top. The "
            "feelings are the model's own, written after each council; nothing assigns them.",
        ),
        (
            "Cost",
            "Model fees for each player's own calls; the host's narration is not included. Measured: what the provider billed for each "
            "call, kept in the season's prompt log. Estimated, for seasons whose log has no billing: recorded calls, times a fixed token "
            "budget per kind of call (reasoning included), times the model's OpenRouter list price. The page names the basis it used.",
        ),
    ]
    return [{"term": term, "text": text} for term, text in rows]


def for_report(season: dict[str, Any]) -> list[dict[str, str]]:
    return definitions(season.get("weights") or {}, season.get("index_version", ""), social=season.get("rules") == "social-game")
