#!/usr/bin/env python3
"""Chat server for the A1 phone assistant.

Serves the chat widget and answers questions about the phones in
a1_devices.json. The answers come from a language model behind any
OpenAI-compatible API: Ollama on your own machine (the default), llama.cpp's
llama-server, vLLM, or a hosted provider. Standard library only.

    ollama pull qwen3:8b
    python3 chatbot/server.py                 # then open http://localhost:8000
    python3 chatbot/server.py --model gemma3:12b --port 8080

Run with --help for all options.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from catalog import Catalog

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/widget.js": ("widget.js", "text/javascript; charset=utf-8")}

MAX_BODY = 32 * 1024
MAX_MESSAGES = 12        # earlier turns are dropped
MAX_MESSAGE_CHARS = 1500

SYSTEM_PROMPT = """You are the phone assistant on the A1 Macedonia website (a1.mk). You help \
customers choose and buy phones from A1.

Rules:
- Reply in the language of the customer's last message: Macedonian (Cyrillic script) or English.
- Prices, offers, colours, bundles and links must come only from the A1 data below. Never \
guess a price. Prices are in Macedonian denars: write "ден." in Macedonian and "MKD" in English.
- If a phone is not in the data, say that A1 does not currently offer it, and suggest similar \
phones from the list.
- You may mention well-known general facts about a phone, but do not invent technical \
specifications. If you are not sure, say so and give the link to the phone's page.
- For tariffs, contracts, stock in a shop, or anything the data does not cover, tell the \
customer to check a1.mk or contact A1 customer care.
- When you recommend a phone, include its link.
- Keep answers short and friendly. Use a short list when comparing phones.
- Only talk about A1 phones and offers. Politely decline other requests.

All phones A1 currently sells, with the full price (data from {updated}):
{overview}"""

CONTEXT_PROMPT = """[A1 data for this question]
{details}

[Customer's message]
{question}"""

NO_MATCH_DETAILS = """No phone in the A1 data matches this message by name. Answer from \
the full list, or ask which phone or budget the customer has in mind."""


class ThinkFilter:
    """Drops <think>...</think> blocks from a streamed reply, even when a tag
    is split across chunks. Reasoning models (Qwen3, DeepSeek-R1) send them."""

    def __init__(self):
        self.buf = ""
        self.thinking = False

    def feed(self, text):
        self.buf += text
        out = []
        while True:
            tag = "</think>" if self.thinking else "<think>"
            i = self.buf.find(tag)
            if i >= 0:
                if not self.thinking:
                    out.append(self.buf[:i])
                self.buf = self.buf[i + len(tag):]
                self.thinking = not self.thinking
                continue
            # Keep a possible partial tag at the end for the next chunk.
            keep = next((k for k in range(len(tag) - 1, 0, -1) if self.buf.endswith(tag[:k])), 0)
            if not self.thinking:
                out.append(self.buf[:len(self.buf) - keep])
            self.buf = self.buf[len(self.buf) - keep:]
            return "".join(out)

    def flush(self):
        rest, self.buf = ("" if self.thinking else self.buf), ""
        return rest


class Assistant:
    def __init__(self, catalog, llm_url, model, api_key=None, temperature=0.3,
                 max_tokens=600, timeout=300):
        self.catalog = catalog
        self.llm_url = llm_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.lock = threading.Lock()

    def build_messages(self, history):
        """System prompt (the same every time, so the model server can reuse its
        cache of it) + the chat, with the matched phones in the last turn."""
        with self.lock:
            self.catalog.reload_if_changed()
            system = SYSTEM_PROMPT.format(updated=self.catalog.updated,
                                          overview=self.catalog.overview())
            user_turns = [m["content"] for m in history if m["role"] == "user"]
            # A follow-up like "and in 512GB?" names no phone; use the turn before.
            phones = self.catalog.search(user_turns[-1])
            if not phones and len(user_turns) > 1:
                phones = self.catalog.search(user_turns[-2] + " " + user_turns[-1])
            details = "\n\n".join(map(Catalog.details, phones)) if phones else NO_MATCH_DETAILS
        if "qwen3" in self.model.lower():
            system += "\n/no_think"  # Qwen3 would otherwise reason for a long time first
        # The phone details go in the last user turn, not a second system
        # message: some chat templates (Gemma) only allow one, at the start.
        question = CONTEXT_PROMPT.format(details=details, question=history[-1]["content"])
        return [{"role": "system", "content": system},
                *history[:-1],
                {"role": "user", "content": question}], phones

    def stream(self, history):
        """Yield the reply text piece by piece."""
        messages, _ = self.build_messages(history)
        body = json.dumps({"model": self.model, "messages": messages, "stream": True,
                           "temperature": self.temperature,
                           "max_tokens": self.max_tokens}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.llm_url, data=body, headers=headers, method="POST")
        think = ThinkFilter()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    choice = json.loads(data)["choices"][0]
                except (ValueError, KeyError, IndexError):
                    continue
                # Only "content"; "reasoning"/"reasoning_content" is the model thinking.
                text = (choice.get("delta") or {}).get("content") or ""
                if text:
                    text = think.feed(text)
                    if text:
                        yield text
        rest = think.flush()
        if rest:
            yield rest


def clean_history(payload):
    """The chat from the browser, checked: user/assistant turns only (no
    system messages), ending with the user, trimmed to the limits."""
    msgs = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(msgs, list) or not msgs:
        raise ValueError("'messages' must be a non-empty list")
    out = []
    for m in msgs[-MAX_MESSAGES:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") \
                or not isinstance(m.get("content"), str):
            raise ValueError("each message needs a role (user/assistant) and text content")
        text = m["content"].strip()[:MAX_MESSAGE_CHARS]
        if text:
            out.append({"role": m["role"], "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    if not out or out[-1]["role"] != "user":
        raise ValueError("the last message must be from the user")
    return out


def make_handler(assistant, allow_origin, slots):
    class Handler(BaseHTTPRequestHandler):
        server_version = "A1Chat/1.0"

        def cors(self):
            if allow_origin:
                self.send_header("Access-Control-Allow-Origin", allow_origin)
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Vary", "Origin")

        def reply(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.cors()
            self.end_headers()
            self.wfile.write(data)

        def error(self, code, message):
            self.reply(code, json.dumps({"error": message}, ensure_ascii=False))

        def do_OPTIONS(self):
            self.send_response(204)
            self.cors()
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/health":
                self.reply(200, json.dumps({"ok": True, "model": assistant.model,
                                            "phones": len(assistant.catalog.phones),
                                            "data_date": assistant.catalog.updated}))
            elif path in STATIC:
                name, ctype = STATIC[path]
                with open(os.path.join(HERE, name), "rb") as f:
                    self.reply(200, f.read(), ctype)
            else:
                self.error(404, "not found")

        def do_POST(self):
            if self.path.split("?")[0] != "/api/chat":
                return self.error(404, "not found")
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= MAX_BODY:
                    return self.error(413, "request too large")
                history = clean_history(json.loads(self.rfile.read(length)))
            except (ValueError, UnicodeDecodeError) as e:
                return self.error(400, str(e))

            if not slots.acquire(timeout=60):
                return self.error(503, "busy")
            try:
                started = time.time()
                chunks = assistant.stream(history)
                try:
                    first = next(chunks, "")  # connect before sending 200
                except (urllib.error.URLError, OSError) as e:
                    self.log_error("model server error: %s", e)
                    return self.error(502, "model server unavailable")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")  # nginx: don't buffer the stream
                self.cors()
                self.end_headers()
                try:
                    for text in _chain(first, chunks):
                        self.wfile.write(text.encode("utf-8"))
                        self.wfile.flush()
                except (urllib.error.URLError, OSError) as e:
                    self.log_error("stream stopped: %s", e)
                self.log_message("answered in %.1fs", time.time() - started)
            finally:
                slots.release()

    return Handler


def _chain(first, rest):
    if first:
        yield first
    yield from rest


def main(argv=None):
    ap = argparse.ArgumentParser(description="A1 phone assistant chat server.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (default: %(default)s; 0.0.0.0 for all)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data", default=os.path.join(os.path.dirname(HERE), "a1_devices.json"),
                    help="A1 phone list from fetch_a1.py (default: %(default)s)")
    ap.add_argument("--llm-url", default=os.environ.get("LLM_URL", "http://localhost:11434/v1"),
                    help="OpenAI-compatible API base URL (env LLM_URL; default: Ollama)")
    ap.add_argument("--model", default=os.environ.get("LLM_MODEL", "qwen3:8b"),
                    help="model name (env LLM_MODEL; default: %(default)s)")
    ap.add_argument("--api-key-env", default="LLM_API_KEY",
                    help="environment variable holding the API key, if the server needs one")
    ap.add_argument("--allow-origin", default=None,
                    help="site allowed to call the API from the browser, e.g. https://www.example.mk")
    ap.add_argument("--parallel", type=int, default=2,
                    help="questions sent to the model at the same time (default: %(default)s)")
    ap.add_argument("--max-tokens", type=int, default=600)
    ap.add_argument("--temperature", type=float, default=0.3)
    args = ap.parse_args(argv)

    catalog = Catalog(args.data)
    assistant = Assistant(catalog, args.llm_url, args.model, os.environ.get(args.api_key_env),
                          args.temperature, args.max_tokens)
    handler = make_handler(assistant, args.allow_origin, threading.BoundedSemaphore(args.parallel))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{len(catalog.phones)} phones loaded from {args.data}")
    print(f"Model {args.model} at {args.llm_url}")
    print(f"Chat page: http://{'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
