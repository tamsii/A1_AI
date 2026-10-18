# A1 phone assistant (chatbot)

A chat bubble for your website that answers questions about A1's phones in
**Macedonian and English**, using the prices in `a1_devices.json`. The answers
are written by a language model that runs on your own computer or server.

```
visitor ──► widget.js (chat bubble on your page)
              │  POST /api/chat
              ▼
           server.py ── finds the phones the question is about in a1_devices.json
              │          and adds their prices, offers, colours and links to the prompt
              ▼
           language model (Ollama by default, any OpenAI-compatible API works)
```

The model is only asked to *write* the answer. Prices come from your data
file, so after `python3 fetch_a1.py` (or `run_all.py`) the bot knows the new
prices straight away, with no restart needed.

Files:

| File | What it does |
|---|---|
| `server.py` | The chat server. Python 3.8+, standard library only. |
| `catalog.py` | Finds phones in a question ("галакси А57", "iphone 17 pro max", "Samsung до 20.000 ден") and formats their data. |
| `widget.js` | The chat bubble. One `<script>` tag adds it to any page. |
| `index.html` | A test page for the bubble. |
| `try_models.py` | Asks several models the same questions and writes their answers side by side. |

## 1. Install Ollama and a model

Ollama runs language models on your own machine (Windows, macOS, Linux).
Download it from <https://ollama.com/download>, then:

```bash
ollama pull qwen3:8b       # recommended start: good Macedonian + English, ~5 GB
```

Other models worth trying (check the exact names at <https://ollama.com/library>):

| Model | Download | Notes |
|---|---|---|
| `qwen3:8b` | ~5 GB | Default. Strong multilingual model. |
| `gemma3:12b` | ~8 GB | Often better written Macedonian. Needs more memory. |
| `gemma3:4b` / `qwen3:4b` | ~3 GB | For weaker computers. Lower Macedonian quality. |
| domestic-yak 8B | ~5 GB | Trained for Macedonian (Hugging Face: LVSTCK). Weaker in English. Needs importing into Ollama. |

Rough hardware guide for an 8B model:
- **Graphics card with 8 GB+ (NVIDIA) or a Mac with Apple Silicon (16 GB)**: fast, answers start within 1–2 seconds.
- **CPU only, 16 GB RAM**: works, but answers take 10–60 seconds. Fine for testing, slow for real visitors.

## 2. Start the chat server

From the project folder:

```bash
python3 chatbot/server.py
```

Open <http://localhost:8000> and click the red button. `http://localhost:8000/health`
shows the model name and how many phones are loaded.

Options:

| Option | Default | Meaning |
|---|---|---|
| `--model NAME` | `qwen3:8b` (env `LLM_MODEL`) | Model to use |
| `--llm-url URL` | `http://localhost:11434/v1` (env `LLM_URL`) | Where the model runs (OpenAI-compatible API) |
| `--port N` | `8000` | Port for the chat server |
| `--host ADDR` | `127.0.0.1` | Use `0.0.0.0` to accept connections from other computers |
| `--data PATH` | `../a1_devices.json` | The A1 phone list |
| `--allow-origin URL` | none | Your site's address, if the chat server runs on a different domain (e.g. `https://www.mysite.mk`) |
| `--parallel N` | `2` | How many questions go to the model at once. Others wait up to a minute. |
| `--backend` | `auto` | `ollama` (Ollama's own API) or `openai` (any OpenAI-compatible server). `auto` picks `ollama` when the URL uses port 11434. |
| `--num-ctx N` | `8192` | How much text Ollama can read at once |
| `--max-tokens N` | `600` | Longest answer |
| `--temperature T` | `0.3` | Lower is more predictable |

If the model needs an API key (a hosted service), put it in the environment
variable `LLM_API_KEY`. It is never sent to the browser.

### If answers are slow

Most of the wait is the model *reading* the prompt before it writes the
first word, and on a computer without a graphics card that is slow. The server
already keeps the prompt short: it sends only the phones a question names, and
the full list only when the question names no phone. It also switches off
Qwen3's "thinking" step and loads the model as soon as it starts. The server
window shows `first word after Xs` for every question.

To make it faster:
- Run `ollama ps` while it answers. `100% CPU` means no graphics card is used.
- Try a smaller model: `python3 chatbot/server.py --model qwen3:4b` (or `gemma3:4b`).
- Close other heavy programs. The model needs free memory.
- The first question after starting is slower. Later questions reuse what Ollama has already read.

## 3. Compare models

```bash
ollama pull gemma3:12b
python3 chatbot/try_models.py qwen3:8b gemma3:12b
```

This asks 12 questions (Macedonian and English, including a phone A1 does not
sell and an off-topic request) and writes `model_test_results.md` with every
answer and how long it took. Check: are the prices right? Is the Macedonian
natural? Does it refuse the off-topic question? Use `-q "your question"` to ask
your own questions instead.

## 4. Put it on your website

Add this before `</body>` on any page:

```html
<script src="https://chat.mysite.mk/widget.js" defer></script>
```

The bubble talks to the server it was loaded from. Optional attributes:
`data-api="https://chat.mysite.mk"` (a different chat server) and
`data-lang="en"` or `"mk"` (interface language; by default it follows the
page's `<html lang>`). The bot answers in the language of each question.

For real visitors, the chat server should be behind HTTPS. The usual setup is
nginx or Caddy in front of `server.py` (which stays on `127.0.0.1`). If the
chat server is on another domain than your site, start it with
`--allow-origin https://www.mysite.mk`.

## Where can it run?

The model location is just `--llm-url`, so you can change it later without
touching the code:

| Option | Cost | Speed |
|---|---|---|
| Your own PC with Ollama | free | depends on your graphics card; the PC must stay on |
| Rented server (VPS) with a GPU running Ollama | ~€50–300/month | fast |
| Cheap VPS without a GPU running Ollama | ~€10–30/month | slow (CPU only) |
| Hosted model API (any OpenAI-compatible provider) | pay per question | fast, but questions leave your server |

## Limits

- The bot knows only what is in `a1_devices.json`: names, prices, tariff
  offers, colours, bundles and links. It does not know tariff details, shop
  stock or full technical specs, and it is told to send people to a1.mk for those.
- Models can still make mistakes, so the widget says answers are AI-generated.
  Test with `try_models.py` before going live.
- The chat history lives in the visitor's browser tab (sessionStorage) and is
  not stored on the server.
