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

## How we work

Backlog, sprints and code are all here on GitHub: see the [Issues](https://github.com/Campus-Caption/CampusCaption/issues) and the organization's project board. Read [CONTRIBUTING.md](CONTRIBUTING.md) before your first pull request.

## Docker

```bash
docker compose up --build
```

Stop with `Ctrl+C` or `docker compose down`.

## Smoke test

```bash
python scripts/stt_smoke.py sample.wav --engine local --language de --realtime
python scripts/stt_smoke.py sample.wav --engine openai
```
(16 kHz mono WAV: `ffmpeg -i in.mp3 -ar 16000 -ac 1 sample.wav`.)

## Tests

`pytest` never downloads a model. To also run the real-Parakeet test, set `CC_TEST_MODEL_DIR` to a folder that holds
the model. The OpenAI/Mistral clients are tested against mock servers only: run the smoke script with your keys once.
