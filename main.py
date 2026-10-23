import os

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.prompt import Prompt
from rich.table import Table

from comrade import db
from comrade import embed as embedder
from comrade.sources import marxists as fetcher
from comrade import ingest as ingester
from comrade import picker
from comrade import quotes
from comrade import render
from comrade import search as searcher
from comrade.config import config

console = Console()


@click.group()
def cli():
    """Reply to Reddit posts with verbatim quotes from socialist writers."""


def _select_works(work_id):
    works = ingester.load_works()
    if work_id:
        works = [w for w in works if w["id"] == work_id]
        if not works:
            raise click.ClickException(f"No work with id {work_id} in {config.CATALOG_DIR}")
    return works


@cli.command()
@click.option("--work", "work_id", help="Download only this work id from the catalog.")
@click.option("--force", is_flag=True, help="Download again even if the file exists.")
def fetch(work_id, force):
    """Download works that have a url in the catalog into texts/."""
    os.makedirs(config.TEXTS_DIR, exist_ok=True)
    for w in _select_works(work_id):
        path = os.path.join(config.TEXTS_DIR, w["file"])
        if not w.get("url"):
            continue
        if os.path.exists(path) and not force:
            console.print(f"[dim]Have {w['id']}[/]")
            continue
        console.print(f"Fetching [bold]{w['id']}[/] from {w['url']}")
        try:
            fetcher.fetch_work(w, config.TEXTS_DIR, log=lambda m: console.print(f"[dim]{m}[/]"))
        except Exception as e:
            console.print(f"[red]Failed {w['id']}: {e}[/]")


@cli.command("fetch-castro")
@click.option("--year", "years", type=int, multiple=True, help="Only this year (repeatable). Default: 1959 to 1996.")
@click.option("--limit", type=int, help="Stop after adding this many speeches.")
def fetch_castro(years, limit):
    """Download Castro speeches (FBIS translations) from the Castro Speech Database."""
    from comrade.sources import castro_fbis as castro_db

    log = lambda m: console.print(f"[dim]{m}[/]")
    base_dir = config.CATALOG_DIR
    castro_db.prune(base_dir, config.TEXTS_DIR, log)
    added = castro_db.fetch(base_dir, config.TEXTS_DIR, years or castro_db.YEARS, limit, log)
    console.print(f"Added {added} speeches to {castro_db.WORKS_FILE}. Run [bold]python main.py ingest[/] next.")


@cli.command()
@click.option("--work", "work_id", help="Ingest only this work id from the catalog.")
def ingest(work_id):
    """Parse texts listed in the catalog into the database."""
    conn = db.connect()
    works = _select_works(work_id)
    if not work_id:
        keep = {w["id"] for w in works}
        for (old,) in conn.execute("SELECT id FROM works").fetchall():
            if old not in keep:
                db.delete_work(conn, old)
                console.print(f"[dim]Removed {old} (no longer in the catalog)[/]")
        conn.commit()

    table = Table("id", "sections", "paragraphs", "sentences")
    for w in works:
        path = os.path.join(config.TEXTS_DIR, w["file"])
        if not os.path.exists(path):
            console.print(f"[yellow]Skipping {w['id']}: {path} not found[/]")
            continue
        sections = ingester.build_sections(path, w)
        n_paragraphs = db.insert_work(conn, w, sections)
        n_sentences = sum(len(s) for _, ps in sections for _, s in ps)
        table.add_row(w["id"], str(len(sections)), str(n_paragraphs), str(n_sentences))
    console.print(table)
    n_quotes = quotes.extract(conn, [work_id] if work_id else None)
    console.print(f"Extracted {n_quotes} quote candidates. Run [bold]python main.py embed[/] to update embeddings.")


@cli.command()
def embed():
    """Embed quotes and summaries that don't have a vector yet."""
    conn = db.connect()
    todo = len(embedder.missing(conn))
    if not todo:
        console.print("All quotes and summaries are embedded.")
        return
    with Progress(console=console) as progress:
        task = progress.add_task(f"Embedding with {config.EMBED_MODEL}", total=todo)
        embedder.embed_missing(conn, progress=lambda n: progress.advance(task, n))


@cli.command()
@click.option("--author", help="Only rate this author's paragraphs (substring match).")
@click.option("--limit", type=int, help="Stop after this many paragraphs.")
@click.option("--workers", default=8, show_default=True, help="Parallel LLM requests.")
def rate(author, limit, workers):
    """Rate quote candidates with the LLM and write summaries. Resumes where it stopped."""
    conn = db.connect()
    todo = len(quotes._unrated(conn, author)[:limit])
    if not todo:
        console.print(f"All paragraphs are rated with {config.LLM_MODEL}.")
        return
    with Progress(console=console) as progress:
        task = progress.add_task(f"Rating with {config.LLM_MODEL}", total=todo)
        rated, chosen = quotes.rate(conn, author, limit, workers=workers,
                                    progress=lambda n: progress.advance(task, n),
                                    warn=lambda m: console.print(f"[yellow]{m}[/]"))
    console.print(f"Rated {rated} paragraphs and chose {chosen} quotes. Run [bold]python main.py embed[/] next.")


@cli.command()
@click.argument("text")
@click.option("--author", help="Only search this author (substring match).")
@click.option("-k", default=10, show_default=True, help="Number of results.")
def search(text, author, k):
    """Show retrieved quotes for TEXT, without calling the LLM."""
    conn = db.connect()
    for q in _search(conn, text, author, k):
        rating = "unrated" if q["rating"] is None else f"rated {q['rating']}"
        note = f"{rating}, score {q['score']:.4f}" + (f": {q['summary']}" if q.get("summary") else "")
        console.print(Panel(q["text"], title=f"[{q['id']}] {render.citation(q)}", subtitle=note,
                            title_align="left", subtitle_align="left"))


@cli.command()
@click.argument("text")
@click.option("--author", help="Only quote this author (substring match).")
def quote(text, author):
    """Pick and print quotes for TEXT, without Reddit."""
    conn = db.connect()
    _show(_pick(conn, text, author))


@cli.command()
@click.argument("url")
@click.option("--author", help="Only quote this author (substring match).")
@click.option("--dry-run", is_flag=True, help="Show candidates without posting.")
def reply(url, author, dry_run):
    """Reply to the Reddit post or comment at URL."""
    from comrade import reddit

    conn = db.connect()
    r = reddit.client()
    thing, text = reddit.fetch(r, url)
    console.print(Panel(text[:2000], title="Target", title_align="left"))

    choices = _pick(conn, text, author)
    _show(choices)
    if dry_run or not choices:
        return

    answer = Prompt.ask("Post which?", choices=[str(i) for i in range(1, len(choices) + 1)] + ["s"], default="1")
    if answer == "s":
        return
    c = choices[int(answer) - 1]
    reddit_id, permalink = reddit.reply(thing, c["body"])
    db.record_post(conn, c["passage"]["id"], c["start"], c["end"], reddit_id, permalink)
    console.print(f"[green]Posted:[/] {permalink}")


@cli.command()
@click.option("--port", default=8765, show_default=True)
def serve(port):
    """Open a local test page: paste text, see ranked passages and quote picks."""
    from comrade import web

    web.serve(port=port)


def _search(conn, text, author, k=None):
    results = searcher.search(conn, text, k=k, author=author,
                               warn=lambda m: console.print(f"[yellow]{m}[/]"))
    if not results:
        raise click.ClickException("No matching quotes. Did you run ingest?")
    return results


def _pick(conn, text, author) -> list[dict]:
    candidates = _search(conn, text, author)
    with console.status(f"Asking {config.LLM_MODEL} to pick quotes"):
        picks = picker.pick(text, candidates)
    choices = []
    for p in picks:
        try:
            p["body"] = render.render(p["passage"], p["start"], p["end"])
        except render.Rejected as e:
            console.print(f"[red]Rejected pick: {e}[/]")
            continue
        choices.append(p)
    if not choices:
        console.print("[yellow]No suitable quote found.[/]")
    return choices


def _show(choices):
    for i, c in enumerate(choices, 1):
        console.print(Panel(c["body"], title=f"{i}", subtitle=c["reason"], title_align="left"))


if __name__ == "__main__":
    cli()
