"""Главная программа: трей, определение звонка, плашка, запись, очередь расшифровки и отправки."""
import ctypes
import logging
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime

from PIL import Image, ImageDraw
import pystray

from . import config, jobs
from .capture import DualCapture
from .detector import CallTracker, active_call_programs
from .pipeline import Pipeline
from .ui import Ui, enable_dpi_awareness

log = logging.getLogger("mrec")
MIN_KEEP_SEC = 5      # записи короче этого считаем случайными и удаляем


def _icon(recording):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=(243, 139, 168, 255) if recording else (108, 112, 134, 255))
    d.ellipse((24, 24, 40, 40), fill=(255, 255, 255, 255) if recording else (30, 30, 46, 255))
    return img


def _single_instance():
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\MeetingRecorder")
    return ctypes.windll.kernel32.GetLastError() != 183, handle   # 183 = ERROR_ALREADY_EXISTS


class App:
    def __init__(self):
        self.cfg = config.load()
        self.q = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.ui = Ui(self.root)
        self.tracker = CallTracker(self.cfg["min_in_use_sec"], self.cfg["call_end_quiet_sec"])
        self.capture = None
        self.job = None
        self.pipeline = Pipeline(self.cfg, self.notify, lambda: self.capture is not None)
        self.tray = pystray.Icon("MeetingRecorder", _icon(False), "Рекордер встреч", self._menu())
        self._quit = False

    # ---------- потоки и команды ----------
    def post(self, fn, *args):
        self.q.put((fn, args))

    def _pump(self):
        try:
            while True:
                fn, args = self.q.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    log.exception("ошибка в команде %s", getattr(fn, "__name__", fn))
        except queue.Empty:
            pass
        if not self._quit:
            self.root.after(200, self._pump)

    def _poll_calls(self):
        while not self._quit:
            try:
                programs = active_call_programs(self.cfg)
                browser_names = set(self.cfg["browser_title_labels"].values()) | {"Browser"}
                only_browser = bool(programs) and all(p in browser_names for p in programs)
                event = self.tracker.update(time.monotonic(), programs,
                                            self.cfg["browser_min_in_use_sec"] if only_browser else None)
                if event:
                    log.info("звонок: %s (%s)", event, ", ".join(programs) or "-")
                if event == "started" and self.capture is None:
                    self.post(self.offer_recording, list(programs))
            except Exception:
                log.exception("ошибка определения звонка")
            time.sleep(1.0)

    # ---------- запись ----------
    def offer_recording(self, programs):
        self.ui.show_plaque(programs, lambda: self.start_recording(programs[0]), lambda: None,
                            self.cfg["plaque_timeout_sec"])

    def start_recording(self, program="Manual"):
        if self.capture is not None:
            return
        job = jobs.new_job(program)
        capture = DualCapture(job.dir / "mic.wav", job.dir / "system.wav")
        try:
            capture.start()
        except Exception as e:
            log.exception("не удалось начать запись")
            jobs.delete_job(job)
            self.notify("Не удалось начать запись", str(e)[:200])
            return
        self.capture, self.job = capture, job
        self.ui.show_indicator(lambda: self.post(self.stop_recording))
        self._refresh_tray()
        log.info("запись началась: %s", job.name)

    def stop_recording(self):
        if self.capture is None:
            return
        capture, job = self.capture, self.job
        self.capture = self.job = None
        self.ui.hide_indicator()
        duration = capture.stop()
        self._refresh_tray()
        if duration < MIN_KEEP_SEC:
            jobs.delete_job(job)
            self.notify("Запись слишком короткая", "Меньше %d секунд, удалена." % MIN_KEEP_SEC)
            return
        job.update(state=jobs.RECORDED, duration=round(duration, 1),
                   ended_at=datetime.now().isoformat(timespec="seconds"),
                   device_changes=capture.device_changes)
        log.info("запись остановлена: %s, %.0f с", job.name, duration)
        self.notify("Запись сохранена", "Расшифровка начнётся сейчас, придёт в топик «Встречи».")
        self.pipeline.poke()

    # ---------- трей ----------
    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem("Остановить запись", lambda: self.post(self.stop_recording),
                             enabled=lambda _i: self.capture is not None),
            pystray.MenuItem("Начать запись вручную", lambda: self.post(self.start_recording),
                             enabled=lambda _i: self.capture is None),
            pystray.MenuItem(lambda _i: "Очередь: " + self.pipeline.status, None, enabled=False),
            pystray.MenuItem("Открыть папку записей", lambda: self._open_folder()),
            pystray.MenuItem("Выход", lambda: self.post(self.quit)),
        )

    def _open_folder(self):
        config.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(config.RECORDINGS_DIR))

    def _refresh_tray(self):
        rec = self.capture is not None
        self.tray.icon = _icon(rec)
        self.tray.title = "Рекордер встреч: идёт запись" if rec else "Рекордер встреч"
        self.tray.update_menu()

    def notify(self, title, message=""):
        try:
            self.tray.notify(message or title, title)
        except Exception:
            log.info("уведомление: %s %s", title, message)

    # ---------- жизненный цикл ----------
    def quit(self):
        if self.capture is not None:
            self.stop_recording()
        self._quit = True
        self.pipeline.stop()
        self.tray.stop()
        self.root.quit()

    def run(self):
        enable_dpi_awareness()
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.tray.run_detached()
        self.pipeline.start()
        threading.Thread(target=self._poll_calls, daemon=True).start()
        self.root.after(200, self._pump)
        self.root.mainloop()


def _log_unhandled():
    """pythonw не показывает ошибки нигде, поэтому всё необработанное пишем в журнал и в crash.log."""
    import faulthandler
    import sys
    import threading

    faulthandler.enable(open(config.DATA_DIR / "crash.log", "a", encoding="utf-8"))
    sys.excepthook = lambda t, v, tb: log.critical("необработанная ошибка", exc_info=(t, v, tb))
    threading.excepthook = lambda a: log.critical("ошибка в потоке %s", a.thread and a.thread.name,
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def main():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(config.LOG_PATH), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    first, _handle = _single_instance()
    if not first:
        return
    _log_unhandled()
    log.info("программа запущена")
    try:
        App().run()
    except BaseException:
        log.critical("программа упала", exc_info=True)
        raise
    log.info("программа закрыта")
