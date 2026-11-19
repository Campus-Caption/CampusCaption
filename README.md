# CampusCaption

Live captions for campus lectures or events. Speak into a microphone, captions appear on a beamer / OBS / any screen.

**There is no login.** Anyone who can reach the server can start captions and use your OpenAI key, so run it on your own
machine (it listens on 127.0.0.1) or inside a network you trust. Add authentication before exposing it publicly.

## Quick start (Windows)

1. Double-click `run.bat` (first run installs everything).
2. Optional: edit `.env` (created automatically) and set `OPENAI_API_KEY=` to enable the cloud engine.
3. Open http://127.0.0.1:8080/, choose an engine, press **Start captions**, allow the microphone.
   `run.bat` downloads the Parakeet model (~465 MB) once during setup and loads it at startup, so the first caption is instant.
4. **Open overlay** shows the captions for the beamer.

Manual start: `pip install -r requirements-local.txt` then `python main.py`. Docker: `docker compose up --build`.

## Layout

- `main.py` - run locally
- `app/api/routes/captions.py` - REST + WebSocket API
- `app/stt/` - speech pipeline: VAD, segmenter, engines (local / OpenAI / Mistral), Claude cleanup, session manager
- `app/templates/`, `frontend/static/` - console (`/`) and overlay (`/overlay`)
- `scripts/stt_smoke.py` - run a WAV through a real engine and report speed
- `tests/` - `pip install -r requirements-dev.txt && pytest`

## Engines

| Engine | Where audio goes | Needs |
|---|---|---|
| **Lokal** (default) | stays on the server | `pip install -r requirements-local.txt`; Parakeet v3 downloads ~465 MB on first use |
| **Cloud – OpenAI Whisper** | OpenAI | `OPENAI_API_KEY` |
| Cloud – Mistral Voxtral Realtime (optional) | Mistral (EU) | `MISTRAL_API_KEY`; hidden until set |
| Claude cleanup (optional toggle) | finished sentences as text go to Anthropic | `ANTHROPIC_API_KEY`; hidden until set |

Claude cannot transcribe audio (the Anthropic API takes no audio); it would only fix punctuation and spellings of
sentences a speech model already produced. Keys go into `.env` only and are never sent to browsers. Local models:
`parakeet` (fast, 25 languages, good on CPU), `whisper-turbo` (99 languages, wants an NVIDIA GPU), `whisper-small`.

## NVIDIA GPU

Whisper uses the GPU automatically (`STT_DEVICE=auto`) once the CUDA libraries from `requirements-local.txt` are installed
(`run.bat` installs them). Check with `.venv\Scripts\python scripts\check_gpu.py`. For a GPU set
`STT_LOCAL_MODEL=whisper-turbo` in `.env` (best accuracy, 99 languages). Parakeet runs on the CPU only.

## How it avoids the prototype's problems

- *Slow:* the prototype re-decoded the growing buffer every time without a limit, so decodes piled up. Now at most one
  preview decode runs at a time, its pace adapts to the machine, and finished sentences go first.
- *Text jumping over 3 lines:* the preview kept changing and was centred, so everything re-wrapped. Now only words
  confirmed by two consecutive decodes become solid (LocalAgreement), and the display is left-aligned and scrolls.
- Silero VAD cuts sentences at pauses and splits very long speech at the latest pause; known Whisper hallucinations
  ("Untertitel der Amara.org-Community") and repetition loops are filtered.

## Overlay options (URL)

`/overlay?session=ID&size=40&lines=3&theme=dark|light|contrast|green|transparent&controls=0&hide=8&off=1`
OBS: browser source with `theme=transparent&controls=0&hide=0`. Buttons: on/off, A-/A+, lines, theme, pop-out
(floating window, Chrome/Edge), fullscreen. Keys: `+` `-` `f` `c`.

## API

| Method | Path |
|---|---|
| GET | `/api/captions/engines` |
| POST | `/api/captions/engines/local/{model}/warm` |
| POST | `/api/captions/session` |
| GET | `/api/captions/session/{id}` · `/latest` |
| PATCH | `/api/captions/session/{id}/settings` |
| POST | `/api/captions/session/{id}/clear` · DELETE `/session/{id}` |
| GET | `/api/captions/session/{id}/transcript?format=txt\|vtt\|srt\|json` |
| WS | `/api/captions/ws/ingest/{id}` (mic: binary 16 kHz mono PCM16 + JSON `start`/`stop`/`ping`) |
| WS | `/api/captions/ws/listen/{id}` (caption feed) |

Interactive docs: http://127.0.0.1:8080/docs

## Check an engine before a lecture

```bash
python scripts/stt_smoke.py sample.wav --engine local --language de --realtime
python scripts/stt_smoke.py sample.wav --engine openai
```
(16 kHz mono WAV: `ffmpeg -i in.mp3 -ar 16000 -ac 1 sample.wav`.)

## Tests

`pytest` never downloads a model. To also run the real-Parakeet test, set `CC_TEST_MODEL_DIR` to a folder that holds
the model. The OpenAI/Mistral clients are tested against mock servers only: run the smoke script with your keys once.
