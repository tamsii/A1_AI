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
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from catalog import Catalog

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/widget.js": ("widget.js", "text/javascript; charset=utf-8")}

MAX_BODY = 32 * 1024
MAX_MESSAGES = 8         # earlier turns are dropped
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

Each customer message comes with the A1 data for it (prices from {updated})."""

CONTEXT_PROMPT = """[A1 data for this question]
{details}

[Customer's message]
{question}"""

NO_MATCH_DETAILS = """No phone matches this message by name. All phones A1 sells, with \
the full price:
{overview}

Answer from this list, or ask which phone or budget the customer has in mind."""


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
                 max_tokens=600, timeout=300, backend="auto", num_ctx=8192):
        self.catalog = catalog
        self.llm_url = llm_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.num_ctx = num_ctx
        if backend == "auto":
            backend = "ollama" if ":11434" in self.llm_url else "openai"
        self.backend = backend
        self.lock = threading.Lock()

    @property
    def ollama_root(self):
        return re.sub(r"/v1$", "", self.llm_url)

    def post(self, url, payload):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        return urllib.request.urlopen(req, timeout=self.timeout)

    def warm_up(self):
        """Load the model into memory now, so the first visitor doesn't wait for it."""
        if self.backend != "ollama":
            return
        started = time.time()
        try:
            with self.post(self.ollama_root + "/api/chat",
                           {"model": self.model, "messages": [], "keep_alive": "30m"}) as r:
                r.read()
            print(f"Model {self.model} loaded in {time.time() - started:.0f}s")
        except urllib.error.HTTPError as e:
            hint = f" Run: ollama pull {self.model}" if e.code == 404 else ""
            print(f"WARNING: could not load {self.model}: {e}.{hint}", file=sys.stderr)
        except OSError as e:
            print(f"WARNING: Ollama is not reachable at {self.ollama_root} ({e}). "
                  f"Is Ollama running?", file=sys.stderr)

    def build_messages(self, history):
        """Short system prompt + the chat, with the matched phones in the last
        turn. Every word the model has to read costs time on a CPU, so only the
        phones the question is about are sent, and the full list only when the
        question names none."""
        with self.lock:
            self.catalog.reload_if_changed()
            system = SYSTEM_PROMPT.format(updated=self.catalog.updated)
            user_turns = [m["content"] for m in history if m["role"] == "user"]
            # A follow-up like "and in 512GB?" names no phone; use the turn before.
            phones = self.catalog.search(user_turns[-1])
            if not phones and len(user_turns) > 1:
                phones = self.catalog.search(user_turns[-2] + " " + user_turns[-1])
            details = ("\n\n".join(map(Catalog.details, phones)) if phones
                       else NO_MATCH_DETAILS.format(overview=self.catalog.overview()))
        # The phone details go in the last user turn, not a second system
        # message: some chat templates (Gemma) only allow one, at the start.
        question = CONTEXT_PROMPT.format(details=details, question=history[-1]["content"])
        return [{"role": "system", "content": system},
                *history[:-1],
                {"role": "user", "content": question}], phones

    def unload(self):
        """Free the model's memory in Ollama now instead of after keep_alive."""
        if self.backend != "ollama":
            return
        try:
            with self.post(self.ollama_root + "/api/chat",
                           {"model": self.model, "messages": [], "keep_alive": 0}) as r:
                r.read()
        except OSError:
            pass

    def stream(self, history):
        """Yield the reply text piece by piece."""
        messages, _ = self.build_messages(history)
        think = ThinkFilter()
        chunks = self.stream_ollama(messages) if self.backend == "ollama" \
            else self.stream_openai(messages)
        for text in chunks:
            text = think.feed(text)
            if text:
                yield text
        rest = think.flush()
        if rest:
            yield rest

    def stream_ollama(self, messages):
        """Ollama's own /api/chat: lets us switch thinking off for real, set the
        context size (Ollama's default is small and cuts long prompts), and keep
        the model loaded between questions."""
        payload = {"model": self.model, "messages": messages, "stream": True,
                   "think": False, "keep_alive": "30m",
                   "options": {"temperature": self.temperature, "num_predict": self.max_tokens,
                               "num_ctx": self.num_ctx}}
        try:
            resp = self.post(self.ollama_root + "/api/chat", payload)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            del payload["think"]  # older Ollama, or a model without a thinking switch
            resp = self.post(self.ollama_root + "/api/chat", payload)
        with resp:
            for raw in resp:
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if obj.get("error"):
                    raise OSError(f"Ollama: {obj['error']}")
                text = (obj.get("message") or {}).get("content") or ""
                if text:
                    yield text
                if obj.get("done"):
                    break

    def stream_openai(self, messages):
        """Any OpenAI-compatible server (llama.cpp, vLLM, hosted providers)."""
        if "qwen3" in self.model.lower():
            messages = [{**messages[0], "content": messages[0]["content"] + "\n/no_think"},
                        *messages[1:]]  # Qwen3 would otherwise reason for a long time first
        payload = {"model": self.model, "messages": messages, "stream": True,
                   "temperature": self.temperature, "max_tokens": self.max_tokens}
        with self.post(self.llm_url + "/chat/completions", payload) as resp:
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
                    yield text


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


WIDGET_TAG = b'<script src="/widget.js" defer></script>'


def add_widget(html):
    """Put the chat bubble's script tag before </body>."""
    i = html.lower().rfind(b"</body>")
    return html[:i] + WIDGET_TAG + html[i:] if i >= 0 else html + WIDGET_TAG


class Site:
    """A web page saved from the browser (File > Save as, "Webpage, Complete"),
    shown at / with the chat bubble added, for trying the bot on a real page.
    Its "..._files" folder is served next to it."""

    def __init__(self, page):
        self.page = os.path.abspath(page)
        self.root = os.path.dirname(self.page)

    def file(self, url_path):
        """(path on disk, is the main page) for a URL path, or None."""
        if url_path in ("/", "/index.html"):
            return self.page, True
        path = os.path.abspath(os.path.join(self.root, urllib.parse.unquote(url_path).lstrip("/")))
        if os.path.commonpath([path, self.root]) != self.root or not os.path.isfile(path):
            return None
        return path, path == self.page


def make_handler(assistant, allow_origin, slots, site=None):
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
            elif path == "/widget.js" or (path in STATIC and not site):
                name, ctype = STATIC[path]
                with open(os.path.join(HERE, name), "rb") as f:
                    self.reply(200, f.read(), ctype)
            elif site and site.file(path):
                file, is_page = site.file(path)
                with open(file, "rb") as f:
                    data = f.read()
                if is_page:
                    self.reply(200, add_widget(data), "text/html; charset=utf-8")
                else:
                    name = file[:-len(".download")] if file.endswith(".download") else file
                    self.reply(200, data, mimetypes.guess_type(name)[0] or "application/octet-stream")
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
                    first_at = time.time() - started
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
                self.log_message("first word after %.1fs, answer done after %.1fs",
                                 first_at, time.time() - started)
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
    ap.add_argument("--site", metavar="PAGE.html",
                    help="show this saved web page at / with the chat bubble on it "
                         "(for trying the bot on your site; its _files folder is served too)")
    ap.add_argument("--parallel", type=int, default=2,
                    help="questions sent to the model at the same time (default: %(default)s)")
    ap.add_argument("--backend", choices=("auto", "ollama", "openai"), default="auto",
                    help="API style: Ollama's own, or OpenAI-compatible "
                         "(default: ollama when the URL uses port 11434)")
    ap.add_argument("--num-ctx", type=int, default=8192,
                    help="Ollama context size in tokens (default: %(default)s)")
    ap.add_argument("--max-tokens", type=int, default=600)
    ap.add_argument("--temperature", type=float, default=0.3)
    args = ap.parse_args(argv)

    catalog = Catalog(args.data)
    assistant = Assistant(catalog, args.llm_url, args.model, os.environ.get(args.api_key_env),
                          args.temperature, args.max_tokens, backend=args.backend,
                          num_ctx=args.num_ctx)
    threading.Thread(target=assistant.warm_up, daemon=True).start()
    site = None
    if args.site:
        if not os.path.isfile(args.site):
            print(f"ERROR: {args.site} not found", file=sys.stderr)
            return 1
        site = Site(args.site)
    handler = make_handler(assistant, args.allow_origin, threading.BoundedSemaphore(args.parallel),
                           site)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{len(catalog.phones)} phones loaded from {args.data}")
    print(f"Model {args.model} at {args.llm_url} ({assistant.backend} API)")
    if site:
        print(f"Showing {site.page} with the chat bubble")
    print(f"Chat page: http://{'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
