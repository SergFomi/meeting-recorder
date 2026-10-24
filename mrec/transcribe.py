"""Расшифровка записи отдельным процессом: python -m mrec.transcribe <папка задания>

Каждый поток (микрофон и звук клиента) режется на куски до 20 секунд по паузам (Silero VAD),
куски распознаёт GigaAM v3, потом потоки сливаются по времени и из них убирается эхо.
"""
import json
import logging
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from . import aec, merge

log = logging.getLogger("mrec.transcribe")
MODEL_NAME = "v3_e2e_rnnt"


def patch_gigaam():
    """GigaAM читает каждый кусок через ffmpeg, и на каждый вызов мигает окно консоли.
    Наши куски уже 16 кГц моно WAV, поэтому читаем их напрямую: быстрее и без окон."""
    import gigaam.model
    import torch

    def load_audio(path, sample_rate=16000):
        return torch.from_numpy(read_wav16(path).astype(np.float32) / 32768.0)

    gigaam.model.load_audio = load_audio


def read_wav16(path):
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def speech_chunks(pcm, vad_model):
    """Куски речи [(start_sec, end_sec)] не длиннее 20 с."""
    import torch
    from silero_vad import get_speech_timestamps
    audio = torch.from_numpy(pcm.astype(np.float32) / 32768.0)
    ts = get_speech_timestamps(audio, vad_model, sampling_rate=16000, max_speech_duration_s=20,
                               min_silence_duration_ms=350, speech_pad_ms=150, return_seconds=True)
    return [(t["start"], t["end"]) for t in ts]


def transcribe_channel(pcm, gigaam_model, vad_model, label):
    segs = []
    chunks = speech_chunks(pcm, vad_model)
    log.info("%s: %d кусков речи", label, len(chunks))
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "chunk.wav"
        for s, e in chunks:
            part = pcm[int(s * 16000):int(e * 16000)]
            if len(part) < 0.3 * 16000:
                continue
            with wave.open(str(tmp), "wb") as o:
                o.setnchannels(1)
                o.setsampwidth(2)
                o.setframerate(16000)
                o.writeframes(part.tobytes())
            text = str(gigaam_model.transcribe(str(tmp))).strip()
            if text:
                segs.append((round(s, 2), round(e, 2), text))
    return segs


def header_lines(meta):
    started = meta.get("started_at", "")
    dur = int(meta.get("duration", 0))
    return ["Встреча: %s" % meta.get("program", "?"),
            "Дата: %s" % started.replace("T", " ")[:19],
            "Длительность: %d мин %02d с" % (dur // 60, dur % 60)]


def run(job_dir, device=None):
    import torch
    import gigaam
    from silero_vad import load_silero_vad

    job_dir = Path(job_dir)
    meta = json.loads((job_dir / "job.json").read_text(encoding="utf-8-sig"))
    t0 = time.time()
    torch.set_num_threads(4)
    device = device or os.environ.get("MREC_DEVICE") or "cuda"
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    model = gigaam.load_model(MODEL_NAME, device=device)
    patch_gigaam()
    vad = load_silero_vad()
    pcm = {label: read_wav16(job_dir / fname) if (job_dir / fname).exists() else np.zeros(0, np.int16)
           for label, fname in (("mic", "mic.wav"), ("system", "system.wav"))}
    if len(pcm["mic"]) and len(pcm["system"]):
        t = time.time()
        cleaned = aec.cancel_echo_stft(pcm["mic"].astype(np.float32) / 32768.0,
                                       pcm["system"].astype(np.float32) / 32768.0)
        pcm["mic"] = (np.clip(cleaned, -1, 1) * 32767).astype(np.int16)
        log.info("эхо вычтено за %.1f с", time.time() - t)
    result = {label: transcribe_channel(p, model, vad, label) if len(p) else [] for label, p in pcm.items()}
    (job_dir / "segments.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    text = merge.build_transcript(result["mic"], result["system"], header_lines(meta))
    (job_dir / "transcript.txt").write_text(text, encoding="utf-8")
    log.info("готово за %.0f с на %s", time.time() - t0, device)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        run(sys.argv[1])
    except Exception as exc:
        # видеокарта может быть занята или не хватить памяти (4 ГБ, рядом звонок): тогда считаем на процессоре
        if "cuda" not in str(exc).lower() and "out of memory" not in str(exc).lower():
            raise
        log.warning("видеокарта не сработала (%s), считаю на процессоре", exc)
        run(sys.argv[1], device="cpu")
