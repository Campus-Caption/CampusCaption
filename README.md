# CampusCaption

Live captions for campus lectures or events. Speak into a microphone, captions appear on a beamer or any screen.
Speech recognition runs only on local models: audio never leaves this computer.

| Model | Best for | Needs |
|---|---|---|
| NVIDIA Nemotron 3.5 ASR Streaming 0.6B | fastest and lightest | CPU is enough (~475 MB) |
| Parakeet v3 (default) | fast, 25 European languages | CPU is enough (~465 MB) |
| Whisper large-v3-turbo / small | 99 languages | GPU recommended for turbo |
| Mistral Voxtral Mini 4B Realtime | best accuracy | NVIDIA GPU (~16 GB), `pip install -r requirements-voxtral.txt`, ~9 GB download from Hugging Face |

**Lecturer password.** Set `LECTURER_SECRET` in `.env` whenever other people can reach the server. Then starting,
changing, clearing, exporting and ending captions (and streaming the microphone) need that password; the console asks
for it once per browser tab. Viewers (overlay, caption feed) never need it. If it is empty, anyone who can reach the
server can control captions, so only leave it empty on your own machine (127.0.0.1).

**Lecture analysis.** With `ANTHROPIC_API_KEY` set, ending a session sends the transcript (text only) once to
Claude (`CLAUDE_ANALYSIS_MODEL`, default Claude Opus 5.5). The professor gets a report: summary, topic timeline,
where students may get lost, open audience questions, key terms, likely caption errors, review questions and ideas
for the next lecture, plus exact numbers (duration, speaking pace, questions). It can be switched off in the
console, is downloadable as Markdown, and is not stored on the server. Clear this with your data protection
officer before using it with real lectures.

**No audio on disk.** Audio only lives in RAM. `tests/test_no_audio_on_disk.py` streams audio through the whole
pipeline and fails if any file is written or any audio shows up in the logs.

## Quick start (Windows)

1. Double-click `run.bat` (first run installs everything).
2. Optional: edit `.env` (created automatically), e.g. `LECTURER_SECRET=` or `STT_LOCAL_MODEL=`.
3. Open http://127.0.0.1:8080/, choose a speech model, press **Start captions**, allow the microphone.
   `run.bat` downloads the Parakeet model (~465 MB) once during setup and loads it at startup, so the first caption is instant.
4. **Open overlay** shows the captions for the beamer.

Manual start: `pip install -r requirements-local.txt` then `python main.py`. Docker: `docker compose up --build`.

## Students on their phones

Students can follow the captions on their own phones, with no app and no account. They can choose the text size
and colours, and scroll back to the start of the lecture.

1. In `.env` set `HOST=0.0.0.0` and a `LECTURER_SECRET` (the server refuses to listen on the network without one),
   then restart. Windows asks once whether to allow Python through the firewall: allow it for private networks.
2. In the console, **Share with students** shows a room code (e.g. `K7P-42Q`) and a QR code. The caption screen
   on the projector shows them in a corner too (press `j` to hide it).
3. Students scan the QR code, or open `http://<this computer>:8080/watch` and type the code.

Phones must be able to reach this computer (same Wi-Fi, and no client isolation on that network). If the address in the join
link is wrong, for example in Docker or behind a reverse proxy, set `PUBLIC_URL`. **Clear screen** and **End session**
remove the text from the phones as well.

## Layout

- `main.py` - run locally
- `app/api/routes/captions.py` - REST + WebSocket API
- `app/stt/` - speech pipeline: VAD, segmenter, local engines (Nemotron / Parakeet / Whisper / Voxtral), optional Claude cleanup, session manager
- `app/templates/`, `frontend/static/` - console (`/`), overlay (`/overlay`) and the students' phone view (`/watch`, `/j/<code>`)
- `scripts/stt_smoke.py` - run a WAV through a real local model and report speed
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
python scripts/stt_smoke.py sample.wav --language de --realtime
python scripts/stt_smoke.py sample.wav --model whisper-turbo
```
(16 kHz mono WAV: `ffmpeg -i in.mp3 -ar 16000 -ac 1 sample.wav`.)

## Tests

`pytest` never downloads a model. To also run the real-Parakeet test, set `CC_TEST_MODEL_DIR` to a folder that holds
the model.
