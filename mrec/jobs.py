"""Задания на диске: папка на каждую запись, состояние в job.json. Переживает перезапуск программы."""
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from .config import RECORDINGS_DIR

RECORDING, RECORDED, TRANSCRIBED, DELIVERED = "recording", "recorded", "transcribed", "delivered"


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_-]", "", name) or "call"


class Job:
    def __init__(self, directory):
        self.dir = Path(directory)

    @property
    def name(self):
        return self.dir.name

    @property
    def meta(self):
        try:
            return json.loads((self.dir / "job.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}

    def update(self, **fields):
        meta = self.meta
        meta.update(fields)
        tmp = self.dir / "job.json.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.dir / "job.json")

    @property
    def state(self):
        return self.meta.get("state", RECORDED)


def new_job(program, root=RECORDINGS_DIR, now=None):
    """Папка вида 2026-10-22_173045_MicroSIP; секунды в имени не дают столкнуться двум записям."""
    now = now or datetime.now()
    name = "%s_%s" % (now.strftime("%Y-%m-%d_%H%M%S"), _safe(program))
    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    job = Job(directory)
    job.update(state=RECORDING, program=program, started_at=now.isoformat(timespec="seconds"), attempts=0)
    return job


def list_jobs(root=RECORDINGS_DIR):
    root = Path(root)
    if not root.exists():
        return []
    return [Job(p) for p in sorted(root.iterdir()) if (p / "job.json").exists()]


def cleanup_audio(job, keep_days, now=None):
    """Удалить звук доставленной записи старше keep_days; расшифровка остаётся."""
    meta = job.meta
    if meta.get("state") != DELIVERED:
        return False
    delivered = meta.get("delivered_at_ts", 0)
    if (now or time.time()) - delivered < keep_days * 86400:
        return False
    removed = False
    for f in ("mic.wav", "system.wav"):
        p = job.dir / f
        if p.exists():
            p.unlink()
            removed = True
    return removed


def delete_job(job):
    shutil.rmtree(job.dir, ignore_errors=True)
