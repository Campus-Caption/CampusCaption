import sys
import wave
import numpy as np
from faster_whisper import WhisperModel

# Read the WAV file with Python's built-in tools (no "av" needed)
# Usage: python transcribe_file.py path/to/recording.wav [model]
path = sys.argv[1] if len(sys.argv) > 1 else "test.wav"
model_name = sys.argv[2] if len(sys.argv) > 2 else "large-v3-turbo"

with wave.open(path, "rb") as f:
    rate = f.getframerate()
    channels = f.getnchannels()
    data = f.readframes(f.getnframes())

audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
if channels == 2:
    audio = audio.reshape(-1, 2).mean(axis=1)    # stereo -> mono

# Whisper needs 16,000 samples per second
if rate != 16000:
    new_length = int(len(audio) * 16000 / rate)
    audio = np.interp(np.linspace(0, len(audio), new_length, endpoint=False),
                      np.arange(len(audio)), audio).astype(np.float32)

model = WhisperModel(model_name, device="cpu", compute_type="int8")
segments, info = model.transcribe(audio, language="de")

for s in segments:
    print(f"[{s.start:.1f}s - {s.end:.1f}s] {s.text}")