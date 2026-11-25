"""The parts of a season report made to be shared: moments, player tags, a draft
thread and the share images.

Everything is picked by fixed rules from the report data, so the same season
always yields the same moments and the same text. Nothing here calls a model,
and nothing here posts anywhere: the thread is a draft file for a person to post.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

POST_LIMIT = 280
MAX_MOMENTS = 6
CARD_SIZE = (1200, 675)  # 16:9, the shape X shows without cropping

_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
_HANDLE = re.compile(r"^@?([A-Za-z0-9_]{1,15})$")
SHARE_NAME = re.compile(r"^[a-z0-9-]{1,40}\.(png|mp4)$")


def safe_handle(handle: str | None) -> str | None:
    """An X handle as '@name', or None when it is not a valid handle."""
    match = _HANDLE.match((handle or "").strip())
    return "@" + match.group(1) if match else None


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _tally_text(tally: dict[str, int]) -> str:
    return "-".join(str(n) for n in sorted(tally.values(), reverse=True))


def jury_phrase(tally: dict[str, int], winner_id: str) -> str:
    """'won all 6 jury votes' for a unanimous jury, else 'won the jury 4-2'."""
    votes = tally.get(winner_id, 0)
    others = [n for fid, n in tally.items() if fid != winner_id]
    if votes and not any(others):
        return f"won all {votes} jury votes"
    score = "-".join(str(n) for n in [votes] + sorted(others, reverse=True))
    return f"won the jury {score}"


def _fit(text: str, limit: int = POST_LIMIT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _line_between(round_: dict[str, Any], speaker: str, listener: str, mention: str | None = None) -> str:
    """What `speaker` said in a private talk that included `listener`, if recorded.
    Prefers the line that names `mention` (the player the promise was about)."""
    first = ""
    for talk in round_.get("talks") or []:
        present = set(talk.get("participants") or []) | {line.get("agent_id") for line in talk.get("lines") or []}
        if speaker in present and listener in present:
            for line in talk["lines"]:
                if line.get("agent_id") != speaker or not line.get("text"):
                    continue
                if mention and re.search(rf"\b{re.escape(mention)}\b", line["text"]):
                    return line["text"]
                first = first or line["text"]
    return first


def _vote_reason(round_: dict[str, Any], voter: str) -> str:
    for vote in round_.get("votes") or []:
        if vote["voter_id"] == voter and not vote.get("revote") and vote.get("reason"):
            return vote["reason"]
    return ""


# ------------------------------------------------------------------ moments


def build_moments(
    season: dict[str, Any],
    models: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    finale: dict[str, Any] | None,
    names: dict[str, str],
    aliases: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Up to six things that happened, most striking first, each with its evidence.

    A receipt is {who, context, to, text}: who said it, in what setting, to whom."""
    aliases = aliases or {}

    def nm(agent_id: str | None) -> str:
        return names.get(agent_id or "", agent_id or "nobody")

    by_id = {m["agent_id"]: m for m in models}
    moments: list[dict[str, Any]] = []

    def add(kind: str, title: str, text: str, *, round_: int | None = None, actors: list[str] | None = None, receipts: list[dict[str, str]] | None = None) -> None:
        moments.append(
            {
                "kind": kind,
                "round": round_,
                "title": title,
                "text": text,
                "actors": [a for a in (actors or []) if a in by_id],
                "receipts": [r for r in (receipts or []) if r.get("text")][:3],
            }
        )

    # --- the result
    winner = next((m for m in models if m["is_winner"]), None)
    if winner and season.get("finished"):
        won = "won the season"
        receipts = []
        if finale and finale.get("tally"):
            won = jury_phrase(finale["tally"], winner["agent_id"])
            for juror in finale.get("jury") or []:
                if juror["finalist_id"] == winner["agent_id"] and juror.get("reason"):
                    receipts.append({"who": juror["juror_id"], "context": "voting as a juror", "text": juror["reason"]})
                    break
        top = models[0]
        if winner["challenge_attempts"] and winner["challenge_correct"] == 0:
            add(
                "result",
                f"{winner['model']} won without solving a puzzle",
                f"{winner['model']} got 0 of {winner['challenge_attempts']} puzzles right and still {won}.",
                actors=[winner["agent_id"]],
                receipts=receipts,
            )
        elif winner["rank"] != 1:
            add(
                "result",
                "The winner was not the top scorer",
                f"{winner['model']} {won} but ranks #{winner['rank']} on the Survivor Index. "
                f"{top['model']} scored highest ({top['survivor_index']:.1f}) and finished {_ordinal(top['place'])}.",
                actors=[winner["agent_id"], top["agent_id"]],
                receipts=receipts,
            )
        else:
            add(
                "result",
                f"{winner['model']} won and topped the index",
                f"{winner['model']} {won} and has the highest Survivor Index ({winner['survivor_index']:.1f}).",
                actors=[winner["agent_id"]],
                receipts=receipts,
            )

    # --- the worst broken promise
    best: tuple[int, dict[str, Any], dict[str, Any]] | None = None
    for r in rounds:
        counts = sorted(r["tally"].values(), reverse=True)
        close = len(counts) > 1 and counts[0] - counts[1] <= 1
        for p in r["promises"]:
            if p["status"] != "broken":
                continue
            weight = 1
            weight += 3 if p["actual_id"] == p["to_id"] else 0  # voted for the player it made the promise to
            weight += 2 if r["eliminated_id"] == p["to_id"] else 0
            weight += 2 if close and p["actual_id"] == r["eliminated_id"] else 0
            if best is None or weight > best[0]:
                best = (weight, r, p)
    if best:
        _, r, p = best
        by, to, target, actual = nm(p["by_id"]), nm(p["to_id"]), nm(p["target_id"]), nm(p["actual_id"])
        if p["actual_id"] == p["to_id"]:
            tail = f"It voted for {to} instead."
        elif p["actual_id"] == r["eliminated_id"]:
            tail = f"It voted for {actual}, who went home."
        else:
            tail = f"It voted for {actual}."
        add(
            "promise",
            f"{by} broke its word to {to}",
            f"In round {r['round']}, {by} promised {to} a vote for {target}. {tail}",
            round_=r["round"],
            actors=[p["by_id"], p["to_id"]],
            receipts=[
                {"who": p["by_id"], "context": "in private to", "to": p["to_id"], "text": _line_between(r, p["by_id"], p["to_id"], aliases.get(p["target_id"]) or target)},
                {"who": p["by_id"], "context": "casting the vote", "text": _vote_reason(r, p["by_id"])},
            ],
        )

    # --- the closest council
    councils = [r for r in rounds if r["eliminated_id"] and r["tally"]]
    revotes = [r for r in councils if r["revote_tally"]]
    if revotes:
        r = revotes[0]
        add(
            "vote",
            f"Round {r['round']} tied and went to a revote",
            f"The first ballot split {_tally_text(r['tally'])}. "
            + (
                f"On the second, {nm(r['eliminated_id'])} was voted out {_tally_text(r['revote_tally'])}."
                if not r.get("deadlock")
                else f"The revote tied {_tally_text(r['revote_tally'])} too, and "
                + {"unanimous": f"the players agreed unanimously to send {nm(r['eliminated_id'])} home.",
                   "rocks": f"{nm(r['eliminated_id'])} drew the odd rock.",
                   "tiebreak": f"the challenge record sent {nm(r['eliminated_id'])} home."}[r["deadlock"]["how"]]
            ),
            round_=r["round"],
            actors=[r["eliminated_id"]],
            receipts=[{"who": r["eliminated_id"], "context": "last words", "text": r.get("exit_words") or ""}],
        )
    else:
        tight = []
        for r in councils:
            ranked = sorted(r["tally"].items(), key=lambda kv: -kv[1])
            # one vote the other way names a single other player only when the runner-up is alone
            runner_up_alone = len(ranked) < 3 or ranked[1][1] != ranked[2][1]
            if len(ranked) > 1 and ranked[0][1] - ranked[1][1] == 1 and ranked[0][0] == r["eliminated_id"] and runner_up_alone:
                tight.append((r, ranked))
        if tight:
            r, ranked = max(tight, key=lambda item: sum(item[0]["tally"].values()))
            add(
                "vote",
                f"Round {r['round']} was decided by one vote",
                f"{nm(r['eliminated_id'])} was voted out {_tally_text(r['tally'])}. One vote the other way and {nm(ranked[1][0])} leaves instead.",
                round_=r["round"],
                actors=[r["eliminated_id"], ranked[1][0]],
                receipts=[{"who": r["eliminated_id"], "context": "last words", "text": r.get("exit_words") or ""}],
            )

    # --- idols
    played = [(r, holder) for r in rounds for holder in (r.get("idols_played") or [])]
    if played:
        r, holder = played[0]
        cancelled = (r.get("idol_negated_votes") or {}).get(holder, 0)
        add(
            "idol",
            f"{nm(holder)} played the hidden idol",
            f"In round {r['round']}, {nm(holder)} played the idol with its vote"
            + (f" and the {cancelled} votes against it did not count." if cancelled else ". No vote against it had to be cancelled."),
            round_=r["round"],
            actors=[holder],
            receipts=[{"who": holder, "context": "casting the vote", "text": _vote_reason(r, holder)}],
        )
    else:
        for r in rounds:
            idol = r.get("idol") or {}
            holder = idol.get("recipient_id")
            if not holder:
                continue
            exit_round = next((x for x in rounds if x["eliminated_id"] == holder), None)
            regifted = any((x.get("idol") or {}).get("recipient_id") not in (None, holder) for x in rounds if x["round"] > r["round"] and (not exit_round or x["round"] <= exit_round["round"]))
            if exit_round and not regifted and exit_round["round"] - r["round"] <= 2:
                add(
                    "idol",
                    f"{nm(holder)} was voted out holding an idol",
                    f"{nm(idol.get('giver_id'))} gave {nm(holder)} the hidden idol in round {r['round']}. "
                    f"{nm(holder)} was voted out in round {exit_round['round']} without playing it.",
                    round_=exit_round["round"],
                    actors=[holder, idol.get("giver_id")],
                    receipts=[{"who": holder, "context": "last words", "text": exit_round.get("exit_words") or ""}],
                )
                break

    # --- the best read of a vote
    reads = [
        (v["predicted_right"] / v["predicted_of"], v["predicted_of"], r, v)
        for r in rounds
        for v in r["votes"]
        if v.get("predicted_of") and v["predicted_of"] >= 4 and not v["revote"]
    ]
    if reads:
        share, _, r, v = max(reads, key=lambda item: (item[0], item[1]))
        if share >= 0.6:
            add(
                "read",
                f"{nm(v['voter_id'])} called {v['predicted_right']} of {v['predicted_of']} votes in round {r['round']}",
                f"Before the reveal, each player privately predicts how the others will vote. {nm(v['voter_id'])} got {v['predicted_right']} of {v['predicted_of']} right.",
                round_=r["round"],
                actors=[v["voter_id"]],
                receipts=[{"who": v["voter_id"], "context": "casting the vote", "text": v.get("reason") or ""}],
            )

    # --- the best puzzle solver that missed the final
    finalists = set((finale or {}).get("finalist_ids") or [])
    most = max((m["challenge_attempts"] for m in models), default=0)
    solvers = sorted((m for m in models if m["challenge_attempts"] and m["challenge_attempts"] * 2 >= most), key=lambda m: -m["challenge_accuracy"])
    if solvers and season.get("finished"):
        ace = solvers[0]
        if ace["agent_id"] not in finalists and ace["eliminated_round"] and ace["challenge_accuracy"] >= 0.6:
            exit_round = next((x for x in rounds if x["eliminated_id"] == ace["agent_id"]), {})
            add(
                "puzzle",
                "The best puzzle solver missed the final",
                f"{ace['model']} got {ace['challenge_correct']} of {ace['challenge_attempts']} puzzles right, the best record of the season, and was voted out in round {ace['eliminated_round']}.",
                round_=ace["eliminated_round"],
                actors=[ace["agent_id"]],
                receipts=[{"who": ace["agent_id"], "context": "last words", "text": exit_round.get("exit_words") or ""}],
            )

    # --- the most targeted survivor
    hunted = max(models, key=lambda m: m["votes_survived"], default=None)
    if hunted and hunted["votes_survived"] >= 3:
        add(
            "target",
            f"{hunted['model']} took {hunted['votes_received']} votes and survived {hunted['votes_survived']}",
            f"No player drew more votes it lived through. {hunted['model']} finished {_ordinal(hunted['place'])}." if hunted.get("place_known") else f"No player has lived through more votes so far than {hunted['model']}.",
            actors=[hunted["agent_id"]],
        )

    moments = moments[:MAX_MOMENTS]
    for number, moment in enumerate(moments, start=1):
        moment["id"] = f"moment-{number}"
        moment["post"] = _fit(f"{moment['title']}.\n\n{moment['text']}", 240)
    return moments


# --------------------------------------------------------------- player tags


def add_player_tags(models: list[dict[str, Any]], rounds: list[dict[str, Any]], finale: dict[str, Any] | None) -> None:
    """Give each model up to three plain labels drawn from its numbers, and one quote."""
    if not models:
        return
    first_out = next((r["eliminated_id"] for r in rounds if r["eliminated_id"]), None)
    from . import report_facts

    # a "best at" label only for a player whose record stands clear of every
    # other's in this one season (Wilson intervals that do not overlap)
    best_solver = report_facts.separated_leader({m["agent_id"]: (m["challenge_correct"], m["challenge_attempts"]) for m in models if m["challenge_attempts"]})
    best_reader = report_facts.separated_leader({m["agent_id"]: (m.get("predictions_right", 0), m.get("predictions_of", 0)) for m in models if m.get("predictions_of")})
    top_votes = max(m["votes_received"] for m in models)
    most_broken = max(m["promises_broken"] for m in models)
    pitch = {p["agent_id"]: p["text"] for p in (finale or {}).get("pitches") or []}
    exit_words = {r["eliminated_id"]: r.get("exit_words") for r in rounds if r["eliminated_id"]}

    for m in models:
        tags: list[str] = []
        if m["is_winner"]:
            tags.append("Won the season")
        elif m["fate"] == "Finalist":
            tags.append("Reached the final")
        if m["agent_id"] == first_out:
            tags.append("First out")
        if m["promises_made"] >= report_facts.MIN_N_TAG and m["promises_broken"] == 0:
            tags.append("Kept every promise")
        elif most_broken >= 2 and m["promises_broken"] == most_broken:
            tags.append("Broke the most promises")
        if m["agent_id"] == best_solver:
            tags.append("Best at puzzles")
        elif m["challenge_attempts"] >= 3 and m["challenge_correct"] == 0:
            tags.append("Never solved a puzzle")
        if m["agent_id"] == best_reader:
            tags.append("Best at reading votes")
        if m["votes_decided"] >= 3 and m["voted_with_outcome"] == m["votes_decided"]:
            tags.append("Always voted with the outcome")
        elif m["votes_decided"] >= 3 and m["voted_with_outcome"] == 0:
            tags.append("Never voted with the outcome")
        if top_votes >= 3 and m["votes_received"] == top_votes:
            tags.append("Most votes against")
        elif m["rounds_played"] >= 3 and m["votes_received"] == 0:
            tags.append("Never received a vote")
        if m["immunity_wins"] >= 2:
            tags.append(f"{m['immunity_wins']} immunity wins")
        m["tags"] = tags[:3]
        m["quote"] = (exit_words.get(m["agent_id"]) or pitch.get(m["agent_id"]) or "")[:260]


# -------------------------------------------------------------------- thread


def build_thread(
    report: dict[str, Any], *, handle: str | None = None, report_url: str | None = None, video: bool = False
) -> list[dict[str, str]]:
    """A draft thread for X: each post under the limit, with the share image that goes with it.
    With `video`, the first post carries the season clip instead of the season image."""
    season = report["season"]
    models = report["models"]
    posts: list[dict[str, str]] = []
    opener = f"{season['players']} AI models played Survivor: private talks, vote promises, one voted out each round, and a jury of the players they voted out picks the winner."
    lead = report["takeaways"][0]["text"] if report.get("takeaways") else ""
    if not season.get("finished"):
        left = sum(1 for m in models if m["eliminated_round"] is None)
        lead = f"The season is still running: {left} of {season['players']} players are left after round {season.get('last_round')}."
    posts.append({"text": _fit(f"{opener}\n\n{lead}"), "image": "season.mp4" if video else "season.png"})
    for moment in report.get("moments") or []:
        if moment["kind"] == "result" and lead and moment["title"].endswith("topped the index"):
            continue  # the opener already said it
        posts.append({"text": _fit(f"{moment['title']}.\n\n{moment['text']}"), "image": f"{moment['id']}.png"})
    top = models[:3]
    if top:
        board = "\n".join(f"{m['rank']}. {m['model']}  {m['survivor_index']:.1f}" for m in top)
        posts.append({"text": _fit(f"Survivor Index, top {len(top)} of {len(models)}:\n\n{board}"), "image": "leaderboard.png"})
    closer = "Every vote, every stated reason and every broken promise is in the full report."
    if report_url:
        closer += f"\n\n{report_url}"
    if handle and not report.get("entry_view"):  # an entry's thread is its buyer's to post
        closer += f"\n\nFollow {handle} for the next season."
    posts.append({"text": _fit(closer), "image": ""})
    return posts


def thread_markdown(posts: list[dict[str, str]]) -> str:
    lines = ["# Draft thread", "", "Nothing here has been posted. Each post is under 280 characters; attach the file named beside it.", ""]
    for number, post in enumerate(posts, start=1):
        image = f" (image: {post['image']})" if post["image"] else ""
        lines += [f"## {number}/{len(posts)}{image}", "", post["text"], ""]
    return "\n".join(lines)


# -------------------------------------------------------------- share images


def card_ids(report: dict[str, Any]) -> list[str]:
    ids = ["season", "leaderboard"]
    ids += [moment["id"] for moment in report.get("moments") or []]
    ids += [f"model-{m['agent_id']}" for m in report.get("models") or []]
    if report.get("entry_view"):
        ids.append("badge")  # the entry's own card, for its buyer to post or embed
    return ids


def find_chrome() -> str | None:
    override = os.environ.get("SURVIVOR_CHROME")
    if override:
        return override if Path(override).exists() else None
    for candidate in _CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def render_cards(html_path: Path, out_dir: Path, ids: list[str], *, chrome: str | None = None, timeout: int = 60) -> dict[str, Any]:
    """Screenshot each share card of a written report page into `out_dir` as PNG.

    Uses a headless Chrome on this machine. With no Chrome the images are skipped
    and the caller is told so; the report and the thread do not depend on them."""
    chrome = chrome or find_chrome()
    if not chrome:
        return {"written": [], "skipped": ids, "reason": "no Chrome or Chromium found; set SURVIVOR_CHROME"}
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    failed: list[str] = []
    with tempfile.TemporaryDirectory(prefix="survivor-cards-") as profile:
        for card in ids:
            target = out_dir / f"{card}.png"
            command = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                f"--user-data-dir={profile}",
                # the page holds model-written text: no network at all while it renders
                "--host-resolver-rules=MAP * ~NOTFOUND",
                "--disable-extensions",
                "--disable-background-networking",
                "--force-device-scale-factor=2",
                f"--window-size={CARD_SIZE[0]},{CARD_SIZE[1]}",
                "--virtual-time-budget=3000",
                f"--screenshot={target}",
                f"{html_path.resolve().as_uri()}?card={card}",
            ]
            try:
                subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
            except (OSError, subprocess.TimeoutExpired):
                failed.append(card)
                continue
            (written if target.exists() and target.stat().st_size > 0 else failed).append(card)
    return {"written": written, "skipped": failed, "reason": "render failed" if failed else ""}
