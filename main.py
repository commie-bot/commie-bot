import os

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.prompt import Prompt
from rich.table import Table

import db
import embed as embedder
import ingest as ingester
import picker
import render
import search as searcher
from config import config

console = Console()


@click.group()
def cli():
    """Reply to Reddit posts with verbatim quotes from socialist writers."""


@cli.command()
@click.option("--work", "work_id", help="Ingest only this work id from works.yaml.")
def ingest(work_id):
    """Parse texts listed in works.yaml into the database."""
    conn = db.connect()
    works = ingester.load_works()
    if work_id:
        works = [w for w in works if w["id"] == work_id]
        if not works:
            raise click.ClickException(f"No work with id {work_id} in {config.WORKS_FILE}")

    table = Table("id", "sections", "paragraphs", "sentences")
    for w in works:
        path = os.path.join(config.TEXTS_DIR, w["file"])
        if not os.path.exists(path):
            console.print(f"[yellow]Skipping {w['id']}: {path} not found[/]")
            continue
        sections = ingester.build_sections(path)
        n_paragraphs = db.insert_work(conn, w, sections)
        n_sentences = sum(len(s) for _, ps in sections for _, s in ps)
        table.add_row(w["id"], str(len(sections)), str(n_paragraphs), str(n_sentences))
    console.print(table)
    console.print("Run [bold]python main.py embed[/] to update embeddings.")


@cli.command()
def embed():
    """Embed paragraphs that don't have a vector yet."""
    conn = db.connect()
    todo = conn.execute(
        """SELECT COUNT(*) FROM paragraphs p LEFT JOIN embeddings e
           ON e.paragraph_id = p.id AND e.model = ? WHERE e.paragraph_id IS NULL""",
        (config.EMBED_MODEL,),
    ).fetchone()[0]
    if not todo:
        console.print("All paragraphs are embedded.")
        return
    with Progress(console=console) as progress:
        task = progress.add_task(f"Embedding with {config.EMBED_MODEL}", total=todo)
        embedder.embed_missing(conn, progress=lambda n: progress.advance(task, n))


@cli.command()
@click.argument("text")
@click.option("--author", help="Only search this author (substring match).")
@click.option("-k", default=10, show_default=True, help="Number of results.")
def search(text, author, k):
    """Show retrieved passages for TEXT, without calling the LLM."""
    conn = db.connect()
    for p in _search(conn, text, author, k):
        console.print(Panel(p["text"], title=f"[{p['id']}] {render.citation(p)}", title_align="left"))


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
    import reddit

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


def _search(conn, text, author, k=None):
    results = searcher.search(conn, text, k=k, author=author,
                               warn=lambda m: console.print(f"[yellow]{m}[/]"))
    if not results:
        raise click.ClickException("No matching passages. Did you run ingest?")
    return results


def _pick(conn, text, author) -> list[dict]:
    passages = _search(conn, text, author)
    with console.status(f"Asking {config.LLM_MODEL} to pick quotes"):
        picks = picker.pick(text, passages)
    choices = []
    for p in picks:
        try:
            p["body"] = render.render(p["passage"], p["start"], p["end"])
        except render.NotVerbatim as e:
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
