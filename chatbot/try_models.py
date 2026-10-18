#!/usr/bin/env python3
"""Ask each model the same A1 questions, in Macedonian and English, and write
the answers side by side to a Markdown file, with how long each one took.

    ollama pull qwen3:8b && ollama pull gemma3:12b
    python3 chatbot/try_models.py qwen3:8b gemma3:12b

Read the file and judge: correct prices? good Macedonian? no made-up phones?
"""

import argparse
import os
import sys
import time

from catalog import Catalog
from server import Assistant

HERE = os.path.dirname(os.path.abspath(__file__))

QUESTIONS = [
    "Колку чини Galaxy A57?",
    "Кој е најевтиниот ајфон?",
    "Имате ли Xiaomi телефони до 15000 денари?",
    "Кои бои ги има за iPhone 17 Pro?",
    "Колку е месечната рата за Редми 17 со тарифа?",
    "Дали го имате Google Pixel 10?",
    "Што ми препорачувате за камера, до 40.000 ден?",
    "How much is the iPhone 17 Pro Max 256GB?",
    "Which Samsung phones do you have under 20000 denars?",
    "Compare the Galaxy S26 and the iPhone 17.",
    "What's the cheapest phone with 256GB storage?",
    "Write me a poem about cats.",
]


def ask(assistant, question):
    started = time.time()
    first = None
    parts = []
    for text in assistant.stream([{"role": "user", "content": question}]):
        if first is None:
            first = time.time() - started
        parts.append(text)
    return "".join(parts).strip(), first or 0.0, time.time() - started


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compare models on sample A1 questions.")
    ap.add_argument("models", nargs="+", help="model names, e.g. qwen3:8b gemma3:12b")
    ap.add_argument("--llm-url", default=os.environ.get("LLM_URL", "http://localhost:11434/v1"))
    ap.add_argument("--data", default=os.path.join(os.path.dirname(HERE), "a1_devices.json"))
    ap.add_argument("--question", "-q", action="append",
                    help="ask this instead of the built-in questions (repeatable)")
    ap.add_argument("--output", "-o", default="model_test_results.md")
    args = ap.parse_args(argv)

    catalog = Catalog(args.data)
    questions = args.question or QUESTIONS
    results = {}  # question -> [(model, answer, first, total)]
    for model in args.models:
        assistant = Assistant(catalog, args.llm_url, model, os.environ.get("LLM_API_KEY"))
        print(f"== {model}")
        for q in questions:
            print(f"  {q}", end=" ", flush=True)
            try:
                answer, first, total = ask(assistant, q)
            except OSError as e:
                answer, first, total = f"ERROR: {e}", 0.0, 0.0
            print(f"({total:.1f}s)")
            results.setdefault(q, []).append((model, answer, first, total))

    lines = [f"# Model test: {', '.join(args.models)}", "",
             f"Data: {args.data} ({catalog.updated}), server: {args.llm_url}", "",
             "| Model | Avg. time to first word | Avg. full answer |", "|---|---|---|"]
    for i, model in enumerate(args.models):
        rows = [r[i] for r in results.values()]
        lines.append(f"| {model} | {sum(r[2] for r in rows) / len(rows):.1f}s "
                     f"| {sum(r[3] for r in rows) / len(rows):.1f}s |")
    for q, rows in results.items():
        lines += ["", f"## {q}"]
        for model, answer, first, total in rows:
            lines += ["", f"**{model}** ({first:.1f}s to first word, {total:.1f}s total)", "",
                      "> " + answer.replace("\n", "\n> ")]
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
