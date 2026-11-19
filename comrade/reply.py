"""The whole reply path in one place: read the post, search the bank, pick and score quotes."""
from comrade import bank
from comrade import picker
from comrade import search as searcher
from comrade.config import config


def find(conn, text: str, author: str = None, warn=lambda message: None, judge: bool = True) -> dict:
    """Return {plan, candidates, picks} for a post or comment.

    With judge=False, picks holds the unscored shortlist, which costs one LLM call fewer.
    """
    plan = bank.classify(text)
    candidates = bank.search(conn, text, plan, author=author, warn=warn)
    if not candidates:
        warn("no bank matches, searched all quotes")
        candidates = searcher.search(conn, text, k=config.CANDIDATES, author=author, warn=warn)
    picks = picker.shortlist(text, candidates, plan["point"]) if candidates else []
    if judge:
        picks = picker.judge(text, picks, plan["claim"])[:picker.PICKS]
    return {"plan": plan, "candidates": candidates, "picks": picks}
