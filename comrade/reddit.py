import praw
from praw.exceptions import InvalidURL

from comrade.config import config


def client() -> praw.Reddit:
    missing = [k for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD")
               if not getattr(config, k)]
    if missing:
        raise RuntimeError(f"Missing Reddit credentials in .env: {', '.join(missing)}")
    return praw.Reddit(
        client_id=config.REDDIT_CLIENT_ID,
        client_secret=config.REDDIT_CLIENT_SECRET,
        username=config.REDDIT_USERNAME,
        password=config.REDDIT_PASSWORD,
        user_agent=config.REDDIT_USER_AGENT,
    )


def fetch(reddit: praw.Reddit, url: str):
    """Return (thing, text) for a comment or submission URL."""
    try:
        comment = reddit.comment(url=url)
        return comment, comment.body
    except InvalidURL:
        submission = reddit.submission(url=url)
        return submission, f"{submission.title}\n\n{submission.selftext}".strip()


def reply(thing, body: str):
    posted = thing.reply(body)
    return posted.id, f"https://www.reddit.com{posted.permalink}"
