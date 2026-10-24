"""Запись двух потоков: микрофон Сергея и звук из наушников/колонок (WASAPI loopback).

Каждый поток пишется в свой WAV 16 кГц моно. Куски звука привязаны к реальному времени:
loopback не присылает данные, пока ничего не играет, поэтому пропуски добиваются тишиной,
иначе дорожки разъедутся на длинном звонке.
"""
import logging
import threading
import time
import wave

import numpy as np

log = logging.getLogger("mrec.capture")

TARGET_RATE = 16000
GAP_TOLERANCE_SEC = 0.25


class Resampler:
    """Потоковое понижение частоты до 16 кГц: сглаживание окном + линейная интерполяция."""

    def __init__(self, rate, target=TARGET_RATE):
        self.ratio = rate / target
        self.k = max(1, int(round(self.ratio)))
        self.buf = np.zeros(0, np.float32)
        self.pos = 0.0

    def process(self, x):
        if self.ratio == 1.0:
            return x.astype(np.int16)
        self.buf = np.concatenate([self.buf, x.astype(np.float32)])
        n = len(self.buf)
        limit = n - 1 - self.k
        if n < 2 or self.pos > limit:
            return np.zeros(0, np.int16)
        smooth = np.convolve(self.buf, np.ones(self.k, np.float32) / self.k, mode="same") if self.k > 1 else self.buf
        count = int(np.floor((limit - self.pos) / self.ratio)) + 1
        idx = self.pos + np.arange(count) * self.ratio
        out = np.interp(idx, np.arange(n), smooth)
        next_pos = self.pos + count * self.ratio
        drop = max(0, int(np.floor(next_pos)) - self.k)
        self.buf = self.buf[drop:]
        self.pos = next_pos - drop
        return np.clip(out, -32768, 32767).astype(np.int16)


class TimelineWriter:
    """Пишет мономоно-16кГц в WAV и добивает тишиной пропуски относительно общих часов t0."""

    def __init__(self, path, t0, clock=time.monotonic):
        self._w = wave.open(str(path), "wb")
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(TARGET_RATE)
        self.t0 = t0
        self.clock = clock
        self.frames = 0
        self._lock = threading.Lock()
        self.closed = False

    def feed(self, samples):
        """samples: int16 моно 16 кГц, только что пришедший кусок (он закончился «сейчас»)."""
        with self._lock:
            if self.closed:
                return
            expected_end = (self.clock() - self.t0) * TARGET_RATE
            gap = expected_end - len(samples) - self.frames
            if gap > GAP_TOLERANCE_SEC * TARGET_RATE:
                self._w.writeframes(np.zeros(int(gap), np.int16).tobytes())
                self.frames += int(gap)
            self._w.writeframes(samples.tobytes())
            self.frames += len(samples)

    def close(self):
        with self._lock:
            if self.closed:
                return 0.0
            total = int((self.clock() - self.t0) * TARGET_RATE)
            if total > self.frames:
                self._w.writeframes(np.zeros(total - self.frames, np.int16).tobytes())
                self.frames = total
            self._w.close()
            self.closed = True
            return self.frames / TARGET_RATE


def _to_mono(data, channels):
    a = np.frombuffer(data, dtype=np.int16)
    if channels > 1:
        a = a[: len(a) - len(a) % channels].reshape(-1, channels).mean(axis=1)
    return a.astype(np.float32)


class _Stream:
    def __init__(self, kind, writer):
        self.kind = kind          # "mic" | "system"
        self.writer = writer
        self.pa = None
        self.stream = None
        self.device_name = None
        self.resampler = None
        self.channels = 1
        self.errors = 0

    def _pick_device(self, pa_mod, pa):
        wasapi = pa.get_host_api_info_by_type(pa_mod.paWASAPI)
        if self.kind == "mic":
            return pa.get_device_info_by_index(wasapi["defaultInputDevice"])
        return pa.get_default_wasapi_loopback()

    def open(self):
        import pyaudiowpatch as pa_mod
        self.pa = pa_mod.PyAudio()
        try:
            dev = self._pick_device(pa_mod, self.pa)
            self.channels = int(dev["maxInputChannels"])
            self.resampler = Resampler(int(dev["defaultSampleRate"]))
            self.device_name = dev["name"]

            def cb(in_data, frame_count, time_info, status):
                try:
                    out = self.resampler.process(_to_mono(in_data, self.channels))
                    if len(out):
                        self.writer.feed(out)
                except Exception:
                    self.errors += 1
                return (None, pa_mod.paContinue)

            self.stream = self.pa.open(
                format=pa_mod.paInt16, channels=self.channels, rate=int(dev["defaultSampleRate"]),
                input=True, input_device_index=dev["index"], frames_per_buffer=1024, stream_callback=cb)
            log.info("%s: %s, %s Hz, %s ch", self.kind, dev["name"], int(dev["defaultSampleRate"]), self.channels)
        except Exception:
            self.close()
            raise

    def close(self):
        try:
            if self.stream is not None:
                self.stream.stop_stream()
                self.stream.close()
        except Exception:
            pass
        try:
            if self.pa is not None:
                self.pa.terminate()
        except Exception:
            pass
        self.stream = None
        self.pa = None

    def current_default_name(self):
        import pyaudiowpatch as pa_mod
        pa = pa_mod.PyAudio()
        try:
            return self._pick_device(pa_mod, pa)["name"]
        finally:
            pa.terminate()


class DualCapture:
    """Запись звонка: два потока с общими часами. Устройства по умолчанию берутся в момент start()."""

    def __init__(self, mic_path, system_path):
        self.t0 = None
        self._streams = []
        self._paths = (mic_path, system_path)
        self._stop_evt = threading.Event()
        self._watch = None
        self.device_changes = []

    def start(self):
        self.t0 = time.monotonic()
        mic = _Stream("mic", TimelineWriter(self._paths[0], self.t0))
        sysd = _Stream("system", TimelineWriter(self._paths[1], self.t0))
        try:
            mic.open()
            sysd.open()
        except Exception:
            mic.close()
            sysd.close()
            mic.writer.close()
            sysd.writer.close()
            raise
        self._streams = [mic, sysd]
        self._watch = threading.Thread(target=self._watch_devices, daemon=True)
        self._watch.start()

    def elapsed(self):
        return 0.0 if self.t0 is None else time.monotonic() - self.t0

    def _watch_devices(self):
        """Если устройство по умолчанию сменилось (наушники, Bluetooth), переоткрываем поток на новое."""
        while not self._stop_evt.wait(3.0):
            for s in self._streams:
                try:
                    name = s.current_default_name()
                    if name != s.device_name:
                        log.info("%s: устройство сменилось %r -> %r", s.kind, s.device_name, name)
                        self.device_changes.append((round(self.elapsed(), 1), s.kind, name))
                        s.close()
                        s.open()
                except Exception as e:
                    log.warning("%s: не удалось переоткрыть поток: %s", s.kind, e)

    def stop(self):
        """Остановить запись, вернуть длительность в секундах."""
        self._stop_evt.set()
        for s in self._streams:
            s.close()
        durations = [s.writer.close() for s in self._streams]
        return max(durations) if durations else 0.0
