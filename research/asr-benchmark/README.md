# ASR benchmark (first research)

Hajar's first test of Whisper on German lecture-style speech: transcribe a recording, then compare models with word error rate (WER).

| File | What it does |
|---|---|
| `transcribe_file.py` | Transcribes one WAV file with faster-whisper (German) and prints the text with timestamps |
| `compare_wer.py` | Compares the `small` and `large-v3-turbo` outputs against the reference text with jiwer (WER, CER, word alignment) |

## Run it

```bash
pip install -r requirements.txt
python transcribe_file.py path/to/recording.wav large-v3-turbo
python compare_wer.py
```

The first run downloads the model, which takes a while.

## First results (30 s test text)

| Model | WER (rough) | Typical errors |
|---|---|---|
| small | ~30% | English terms (Python → "Beide"), compound words |
| large-v3-turbo | ~14% | "15. Dezember" → "12. Dezember", PyTorch → Python |

Next: try the German fine-tune (`primeline/whisper-large-v3-turbo-german`) and term hints (`initial_prompt` / `hotwords`) with words like PyTorch and Machine Learning.

## Recordings

Recordings are **not** in this repo (see `docs/decisions/0002-no-audio-on-disk.md`). Ask the team for the consented test files.
