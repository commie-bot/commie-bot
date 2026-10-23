"""Transcribe speech recordings to text files that `ingest` can read.

Runs faster-whisper on a GPU. It starts a new paragraph at long pauses, because
recordings have no paragraph breaks.

    pip install faster-whisper
    python -m comrade.sources.transcribe speech.mp3 [more.mp3 ...] --out texts/private/malcolm-x/transcripts
"""
import argparse
import os

PAUSE = 2.0  # seconds of silence that start a new paragraph
MAX_PARAGRAPH = 900  # characters


def transcribe(model, path: str) -> list[str]:
    segments, _ = model.transcribe(path, language="en", vad_filter=True, beam_size=5)
    paragraphs, current, last_end = [], [], 0.0
    for seg in segments:
        text = seg.text.strip()
        long_pause = seg.start - last_end > PAUSE
        too_long = sum(len(t) for t in current) > MAX_PARAGRAPH and current[-1].endswith((".", "?", "!"))
        if current and (long_pause or too_long):
            paragraphs.append(" ".join(current))
            current = []
        current.append(text)
        last_end = seg.end
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+")
    parser.add_argument("--out", required=True, help="Directory for the .txt files.")
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--compute-type", default="float32", help="float32 suits older GPUs; float16 is faster on newer ones.")
    args = parser.parse_args()

    from faster_whisper import WhisperModel

    model = WhisperModel(args.model, device="cuda", compute_type=args.compute_type)
    os.makedirs(args.out, exist_ok=True)
    for path in args.files:
        out = os.path.join(args.out, os.path.splitext(os.path.basename(path))[0] + ".txt")
        paragraphs = transcribe(model, path)
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n\n".join(paragraphs) + "\n")
        print(f"{out}: {len(paragraphs)} paragraphs")


if __name__ == "__main__":
    main()
