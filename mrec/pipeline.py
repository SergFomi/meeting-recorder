"""Очередь: записано -> расшифровано -> доставлено. Каждый шаг можно повторить после сбоя или перезапуска."""
import logging
import os
import subprocess
import sys
import threading
import time
import wave

from . import jobs
from .deliver import deliver

log = logging.getLogger("mrec.pipeline")
BELOW_NORMAL, NO_WINDOW = 0x00004000, 0x08000000
FAILURES_BEFORE_NOTICE = 3


class Pipeline:
    """Фоновый поток: берёт задания по порядку. Пока идёт запись, расшифровку не запускает."""

    def __init__(self, cfg, notify, is_recording):
        self.cfg = cfg
        self.notify = notify
        self.is_recording = is_recording
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._proc = None
        self.status = "ожидание"

    def start(self):
        self._thread.start()

    def poke(self):
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("сбой очереди")
            self._wake.wait(self.cfg["retry_every_sec"])
            self._wake.clear()

    def run_once(self):
        for job in jobs.list_jobs():
            if self._stop.is_set() or self.is_recording():
                return
            self._advance(job)
        for job in jobs.list_jobs():
            jobs.cleanup_audio(job, self.cfg["keep_audio_days"])
        self.status = "ожидание"

    def _advance(self, job):
        state = job.state
        if state == jobs.RECORDING:
            # программа закрылась посреди записи: звук на диске есть, считаем запись законченной
            mic = job.dir / "mic.wav"
            if mic.exists():
                try:
                    with wave.open(str(mic), "rb") as w:
                        job.update(duration=w.getnframes() / w.getframerate())
                except Exception:
                    pass
                job.update(state=jobs.RECORDED)
                state = jobs.RECORDED
            else:
                return
        if state == jobs.RECORDED:
            self.status = "расшифровка: %s" % job.name
            self._transcribe(job)
            state = job.state
        if state == jobs.TRANSCRIBED:
            self.status = "отправка: %s" % job.name
            self._deliver(job)

    def _transcribe(self, job):
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "mrec.transcribe", str(job.dir)],
                cwd=str(_project_root()),
                env={**os.environ, "MREC_DEVICE": self.cfg["asr_device"],
                     "MREC_ME": self.cfg["speaker_me"], "MREC_THEM": self.cfg["speaker_them"]},
                creationflags=BELOW_NORMAL | NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            # пауза, если начался новый звонок: расшифровка не должна мешать
            while self._proc.poll() is None:
                time.sleep(2)
            err = self._proc.stderr.read().decode("utf-8", "replace")
            if self._proc.returncode or not (job.dir / "transcript.txt").exists():
                raise RuntimeError(err.strip().splitlines()[-1] if err.strip() else "код %s" % self._proc.returncode)
            job.update(state=jobs.TRANSCRIBED)
        except Exception as e:
            self._fail(job, "расшифровка", e)
        finally:
            self._proc = None

    def _deliver(self, job):
        try:
            deliver(job, self.cfg["server"], self.cfg["transcripts_dir"])
            job.update(state=jobs.DELIVERED, delivered_at_ts=time.time())
            self.notify("Расшифровка готова" if not self.cfg["server"]["ssh_host"] else "Расшифровка отправлена", job.name)
        except Exception as e:
            self._fail(job, "отправка", e)

    def _fail(self, job, step, error):
        attempts = job.meta.get("attempts", 0) + 1
        job.update(attempts=attempts, last_error="%s: %s" % (step, error))
        log.warning("%s: %s не удалась (попытка %d): %s", job.name, step, attempts, error)
        if attempts == FAILURES_BEFORE_NOTICE:
            self.notify("Не удалось: " + step, "%s. Запись сохранена, повторю позже." % error)


def _project_root():
    from pathlib import Path
    return Path(__file__).resolve().parents[1]
