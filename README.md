# comrade-bot

A CLI that replies to a Reddit post or comment with a verbatim quote from a socialist or communist writer.

The pipeline:

1. Fetch the post or comment text.
2. Retrieve the top 30 paragraphs with hybrid search (SQLite FTS5 BM25 plus Ollama embeddings, merged with reciprocal rank fusion).
3. Ask an LLM to pick up to 3 quotes. The model returns only a passage ID and a sentence range.
4. Build each quote from the stored sentences and check that it's an exact substring of the source paragraph.
5. Show the candidates. You pick one, and the bot posts it.

Because the model never writes quote text, every quote is verbatim.

## Choose an LLM

Set `LLM_PROVIDER`, `LLM_MODEL`, and optionally `LLM_API_KEY` and `LLM_BASE_URL` in `.env`. Two providers are available:

- `anthropic`: Claude models through the Anthropic API.
- `openai`: any OpenAI-compatible chat completions API, such as OpenAI, Ollama, OpenRouter, Groq, vLLM, or LM Studio. To use a server other than OpenAI, set `LLM_BASE_URL`.

| Setup | `LLM_PROVIDER` | `LLM_MODEL` | `LLM_BASE_URL` |
| --- | --- | --- | --- |
| Claude | `anthropic` | `claude-sonnet-5-5` | |
| OpenAI | `openai` | `gpt-5` | |
| Local Ollama | `openai` | `qwen3:8b` | `http://localhost:11434/v1` |
| OpenRouter | `openai` | any model ID | `https://openrouter.ai/api/v1` |

To add another provider, write a function in `llm.py` that takes a system prompt, a user prompt, and a JSON schema and returns a dict. Then register it in `PROVIDERS`.

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

3. Download the starter corpus. `works.yaml` lists about 45 public works by Marx, Engels, Lenin, Stalin, Luxemburg, Mao, Ho Chi Minh, Castro, Guevara, and Debs, all from the [Marxists Internet Archive](https://www.marxists.org/):

   ```bash
   python main.py fetch
   ```

   The fetcher sends one request per second. For multi-page works, it follows the chapter links on the index page and saves each work as one UTF-8 HTML file in `texts/`.

   To add a work, add an entry to `works.yaml`. Use `url` to download it, or put your own file in `texts/` and set `file`. Supported formats: HTML, EPUB, PDF, and TXT.

Texts aren't included in this repo. Check each source's terms before you redistribute them.

## Usage

```bash
python main.py fetch                  # download works that have a url
python main.py ingest                 # parse all works (re-running replaces them)
python main.py ingest --work manifesto
python main.py embed                  # embed new paragraphs
python main.py search "landlords are parasites"        # retrieval only
python main.py quote "landlords are parasites"         # retrieval + LLM, no Reddit
python main.py reply <reddit-url> --dry-run
python main.py reply <reddit-url> --author lenin
```

`search` works without embeddings; it falls back to keyword search and prints a warning.

## Reply format

```
> "The philosophers have only interpreted the world, in various ways; the point is to change it."

**Karl Marx**, *Theses on Feuerbach*, XI (1845)

^(I am a bot. Quotes are verbatim from the cited source.)
```

The work title links to `source_url` when `works.yaml` sets it. The section is omitted when it's empty or matches the title.

## Tests

```bash
python -m pytest
```
