# comrade-bot

A CLI that replies to a Reddit post or comment with a verbatim quote from a socialist or communist writer.

Ahead of time, the bot builds an index of quotes:

1. Split every work into paragraphs and sentences.
2. Extract quote candidates: every run of 1 to 3 sentences, under 300 characters, that can stand alone. Rules drop spans that open with a word like "This" or "Thus", fragments, statistics, and words the author quotes from someone else.
3. Optional: an LLM reads every paragraph once, rates its best quotes from 3 to 5, and writes a one-line summary of each quote's point in plain modern English.
4. Embed each quote and each summary with Ollama.

To reply:

1. Fetch the post or comment text.
2. Retrieve the top 20 quotes with hybrid search: SQLite FTS5 BM25 over the quotes and summaries, plus embeddings of both, merged with reciprocal rank fusion. Higher-rated quotes get a boost, and quotes the LLM passed over get a penalty.
3. Ask an LLM to pick up to 3 of them. The model returns only quote IDs.
4. Build each quote from the stored sentences and check that it's an exact substring of the source paragraph.
5. Show the candidates. You pick one, and the bot posts it.

Because the model never writes quote text, every quote is verbatim. Matching against summaries finds quotes that make the same point as a post in different words, such as "my landlord takes half my paycheck" and a passage on rent absorbing the tenant's profit.

## Choose an LLM

Set `LLM_PROVIDER`, `LLM_API_KEY`, and optionally `LLM_MODEL` and `LLM_BASE_URL` in `.env`. Three providers are available:

- `cerebras` (default): [Cerebras Inference](https://cloud.cerebras.ai). It's fast, and the default model is `gpt-oss-120b`.
- `anthropic`: Claude models through the Anthropic API.
- `openai`: any OpenAI-compatible chat completions API, such as OpenAI, Ollama, OpenRouter, Groq, vLLM, or LM Studio. To use a server other than OpenAI, set `LLM_BASE_URL`.

| Setup | `LLM_PROVIDER` | `LLM_MODEL` | `LLM_BASE_URL` |
| --- | --- | --- | --- |
| Cerebras | `cerebras` | `gpt-oss-120b` (default) or `qwen-3.8-27b` | |
| Claude | `anthropic` | `claude-sonnet-5-5` (default) | |
| OpenAI | `openai` | `gpt-5` | |
| Local Ollama | `openai` | `qwen3:8b` | `http://localhost:11434/v1` |
| OpenRouter | `openai` | any model ID | `https://openrouter.ai/api/v1` |

To add another provider, write a function in `comrade/llm.py` that takes a system prompt, a user prompt, and a JSON schema and returns a dict. Then register it in `PROVIDERS`.

## Setup

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   cp .env.example .env
   ```

2. Fill in `.env`. For embeddings, run Ollama locally and pull the model:

   ```bash
   ollama pull nomic-embed-text
   ```

   To use the server's GPU instead, open a tunnel and keep `OLLAMA_URL=http://localhost:11434`:

   ```bash
   ssh -N -L 11434:127.0.0.1:11434 user@your-server
   ```

3. Download the starter corpus. `catalog/marxists.yaml` lists about 185 public works by Marx, Engels, Lenin, Stalin, Luxemburg, Mao, Ho Chi Minh, Castro, Guevara, Sankara, Hampton, Parenti, Malcolm X, Debs, Kollontai, Zetkin, Nkrumah, Cabral, and others, all from the [Marxists Internet Archive](https://www.marxists.org/):

   ```bash
   python main.py fetch
   ```

   The fetcher sends one request per second. For multi-page works, it follows the chapter links on the index page and saves each work as one UTF-8 HTML file in `texts/fetched/<author>/`.

4. Optional: download about 580 more Castro speeches from the [Castro Speech Database](https://web.archive.org/web/2019/http://lanic.utexas.edu/la/cb/cuba/castro.html), 1959 to 1996. The translations are by the U.S. Foreign Broadcast Information Service (FBIS). The command reads the Internet Archive's copy, so a full run takes a few hours. It saves progress after each speech, so you can stop and restart it.

   ```bash
   python main.py fetch-castro              # all years
   python main.py fetch-castro --year 1962  # one year
   ```

   The command keeps only documents that FBIS marks as speeches. It drops wire reports and editorial summaries, which paraphrase Castro, and speeches that `catalog/marxists.yaml` already has. It also removes FBIS insertions such as "(applause)". The results go to `catalog/castro-fbis.yaml`.

   The FBIS translations are U.S. government works in the public domain, so the repo already includes them in `texts/public/castro/fbis/`. You need this command only to download them again.

5. Optional: transcribe the Malcolm X recordings from the [Marxists Internet Archive](https://www.marxists.org/reference/archive/malcolm-x/index.htm). `comrade/sources/transcribe.py` runs [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and needs a CUDA GPU. Download the MP3 files, then run:

   ```bash
   python -m comrade.sources.transcribe *.mp3 --out texts/private/malcolm-x/transcripts
   ```

   Name each MP3 file after the `file` entry in `catalog/marxists.yaml`, such as `ballot-or-the-bullet.mp3`. Machine transcripts can contain errors, so check a quote against the recording before you post it.

   To add a work, add an entry to `catalog/marxists.yaml`. Use `url` to download it, or put your own file in `texts/` and set `file` to its path there. Supported formats: HTML, EPUB, PDF, and TXT.

   For your own copies of books, put the files in `texts/private/<author>/` and list them in `catalog/private.yaml`. Git ignores both, and an entry with the same `id` as a `marxists.yaml` entry replaces that entry. Books often contain text by other writers, such as introductions, editor's notes, and endnotes. Ingest leaves out common book matter by section title. For anything else, set these fields:

   - `skip_sections`: section titles to leave out, as regular expressions matched against the whole title.
   - `only_sections`: the only sections to keep, for books that collect several authors.
   - `pages`: the first and last PDF page to read.

   To see a book's section titles, run `python -c "from comrade import ingest; print([t for t, _ in ingest.parse_file('texts/private/author/book.epub')])"`.

   Ingest also leaves out block quotes and epigraphs, because they quote other writers.

### Where texts live

| Folder | In git | Contents |
| --- | --- | --- |
| `texts/public/` | Yes | Public domain texts only, such as the FBIS Castro speeches. |
| `texts/fetched/` | No | Works that `fetch` downloads from marxists.org. Their licenses vary, so the repo doesn't redistribute them. |
| `texts/private/` | No | Your own books and transcripts, listed in `catalog/private.yaml`. |

Each folder has one subfolder per author, such as `texts/fetched/lenin/`.

Add a text to `texts/public/` only if it's in the public domain. Never commit `texts/private/` or `catalog/private.yaml`.

## Usage

```bash
python main.py fetch                  # download works that have a url
python main.py ingest                 # parse all works (re-running replaces them)
python main.py ingest --work manifesto
python main.py rate                   # LLM ratings and summaries (resumes; about $11 for the full corpus on Cerebras)
python main.py embed                  # embed new quotes and summaries
python main.py search "landlords are parasites"        # retrieval only
python main.py quote "landlords are parasites"         # retrieval + LLM, no Reddit
python main.py reply <reddit-url> --dry-run
python main.py reply <reddit-url> --author lenin
```

`ingest` also extracts the quote candidates. Run `rate` and then `embed` after you add works. `rate` is optional: without it, every quote is unrated and ranks on its match alone. Ratings and vectors are stored by a hash of the quote text, so re-ingesting a work doesn't redo them.

`search` works without embeddings; it falls back to keyword search and prints a warning.

To test retrieval in a browser, run `python main.py serve` and open `http://127.0.0.1:8765`. Paste text, then click **Search** to see ranked quotes with their ratings and summaries, or **Pick quotes with the LLM** to see the quotes the bot would post. The page listens only on your computer.

## Reply format

```
> "The philosophers have only interpreted the world, in various ways; the point is to change it."

**Karl Marx**, *Theses on Feuerbach*, XI (1845)

^(I am a bot. Quotes are verbatim from the cited source.)
```

The work title links to `source_url` when the catalog sets it. The section is omitted when it's empty or matches the title.

## Layout

```
main.py             CLI
comrade/            retrieval, quote picking, rendering, Reddit, the test page
  sources/          downloaders (marxists.org, Castro FBIS) and the transcriber
catalog/            work lists: marxists.yaml, castro-fbis.yaml, private.yaml
texts/              source files: public/, fetched/, private/
data/               the database and logs (not committed)
tests/
```

## Tests

```bash
python -m pytest
```
