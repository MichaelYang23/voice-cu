"""Push-to-talk speech input: hold a key, speak, release -> local Whisper (MLX).

v0 is non-streaming: transcription starts when you release the key.
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np
import sounddevice as sd
from pynput import keyboard

SR = 16000


class PushToTalk:
    def __init__(self, hotkey: str = "alt_r", model: str = "mlx-community/whisper-large-v3-turbo",
                 language: str | None = None):
        self.hotkey = getattr(keyboard.Key, hotkey, None) or keyboard.KeyCode.from_char(hotkey)
        self.model, self.language = model, language
        self._frames: list[np.ndarray] = []
        self._recording = False
        self._out: queue.Queue = queue.Queue()
        self._stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                                      callback=self._cb, blocksize=1024)
        self._stream.start()
        keyboard.Listener(on_press=self._press, on_release=self._release, daemon=True).start()

    def warmup(self) -> float:
        import mlx_whisper
        t0 = time.perf_counter()
        mlx_whisper.transcribe(np.zeros(SR, dtype=np.float32), path_or_hf_repo=self.model)
        return (time.perf_counter() - t0) * 1000

    def _cb(self, indata, frames, t, status):
        if self._recording:
            self._frames.append(indata[:, 0].copy())

    def _press(self, key):
        if key == self.hotkey and not self._recording:
            self._frames, self._recording = [], True
            self._t_press = time.perf_counter()

    def _release(self, key):
        if key == self.hotkey and self._recording:
            self._recording = False
            audio = np.concatenate(self._frames) if self._frames else np.zeros(0, np.float32)
            threading.Thread(target=self._transcribe, args=(audio, time.perf_counter()),
                             daemon=True).start()

    def _transcribe(self, audio, t_release):
        if len(audio) < SR * 0.25:
            return
        import mlx_whisper
        kw = {"language": self.language} if self.language else {}
        res = mlx_whisper.transcribe(audio, path_or_hf_repo=self.model, **kw)
        text = (res.get("text") or "").strip()
        stt_ms = (time.perf_counter() - t_release) * 1000
        if text:
            self._out.put((text, stt_ms, len(audio) / SR))

    def next(self):
        """Blocks until a transcript is ready. Returns (text, stt_ms, audio_seconds)."""
        return self._out.get()
