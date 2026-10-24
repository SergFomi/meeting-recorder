"""Окна: плашка «Записать?» и мигающий красный индикатор записи в правом верхнем углу экрана.

Окна без рамки, поверх всех, не забирают фокус (WS_EX_NOACTIVATE), поэтому не мешают звонку.
Всё работает в потоке Tk; из других потоков сюда ходят только через App.post().
"""
import ctypes
import time
import tkinter as tk

BG, FG, MUTED = "#1e1e2e", "#cdd6f4", "#6c7086"
RED, DIM_RED, GREEN = "#ff3b3b", "#661a1a", "#a6e3a1"

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _no_activate(win):
    """Не забирать фокус и не показываться в Alt+Tab."""
    try:
        win.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    except Exception:
        pass


def _popup(root):
    w = tk.Toplevel(root)
    w.overrideredirect(True)
    w.attributes("-topmost", True)
    w.configure(bg=BG, highlightthickness=1, highlightbackground=MUTED)
    return w


def _button(parent, text, color, command):
    b = tk.Label(parent, text=text, bg=color, fg="#11111b", padx=14, pady=5, cursor="hand2",
                 font=("Segoe UI", 10, "bold"))
    b.bind("<Button-1>", lambda _e: command())
    return b


class Ui:
    def __init__(self, root):
        self.root = root
        self._plaque = None
        self._indicator = None
        self._confirm = None
        self._blink_on = True
        self._started = None
        self._on_stop = None

    # ---------- плашка ----------
    def show_plaque(self, programs, on_yes, on_no, timeout_sec):
        self.hide_plaque()
        w = self._plaque = _popup(self.root)
        tk.Label(w, text="Идёт звонок · %s" % ", ".join(programs), bg=BG, fg=FG,
                 font=("Segoe UI", 11, "bold"), padx=16, pady=(10)).pack(anchor="w")
        tk.Label(w, text="Записать для расшифровки?", bg=BG, fg=MUTED, font=("Segoe UI", 10),
                 padx=16).pack(anchor="w")
        row = tk.Frame(w, bg=BG)
        row.pack(anchor="w", padx=12, pady=12)
        _button(row, "Записать", GREEN, lambda: (self.hide_plaque(), on_yes())).pack(side="left", padx=4)
        _button(row, "Нет", MUTED, lambda: (self.hide_plaque(), on_no())).pack(side="left", padx=4)
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry("+%d+%d" % (sw - w.winfo_width() - 24, sh - w.winfo_height() - 90))
        _no_activate(w)
        w.after(int(timeout_sec * 1000), lambda: self._plaque is w and (self.hide_plaque(), on_no()))

    def hide_plaque(self):
        if self._plaque is not None:
            try:
                self._plaque.destroy()
            except tk.TclError:
                pass
            self._plaque = None

    # ---------- индикатор записи ----------
    def show_indicator(self, on_stop):
        self.hide_indicator()
        self._on_stop = on_stop
        self._started = time.monotonic()
        w = self._indicator = _popup(self.root)
        self._dot = tk.Canvas(w, width=22, height=22, bg=BG, highlightthickness=0)
        self._dot.pack(side="left", padx=(10, 4), pady=6)
        self._circle = self._dot.create_oval(3, 3, 19, 19, fill=RED, outline="")
        self._label = tk.Label(w, text="REC 00:00", bg=BG, fg=FG, font=("Consolas", 11, "bold"))
        self._label.pack(side="left", padx=(0, 12))
        for widget in (w, self._dot, self._label):
            widget.bind("<Button-1>", lambda _e: self._ask_stop())
            widget.configure(cursor="hand2")
        w.update_idletasks()
        w.geometry("+%d+%d" % (w.winfo_screenwidth() - w.winfo_width() - 24, 14))
        _no_activate(w)
        self._tick()

    def _tick(self):
        w = self._indicator
        if w is None:
            return
        self._blink_on = not self._blink_on
        self._dot.itemconfigure(self._circle, fill=RED if self._blink_on else DIM_RED)
        sec = int(time.monotonic() - self._started)
        self._label.configure(text="REC %02d:%02d" % (sec // 60, sec % 60) if sec < 3600
                              else "REC %d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60))
        w.attributes("-topmost", True)
        w.after(600, self._tick)

    def hide_indicator(self):
        self._close_confirm()
        if self._indicator is not None:
            try:
                self._indicator.destroy()
            except tk.TclError:
                pass
            self._indicator = None

    # ---------- подтверждение остановки ----------
    def _ask_stop(self):
        if self._confirm is not None or self._indicator is None:
            return
        w = self._confirm = _popup(self.root)
        tk.Label(w, text="Остановить запись?", bg=BG, fg=FG, font=("Segoe UI", 11, "bold"),
                 padx=16, pady=10).pack(anchor="w")
        row = tk.Frame(w, bg=BG)
        row.pack(anchor="w", padx=12, pady=(0, 12))
        _button(row, "Остановить", RED, self._do_stop).pack(side="left", padx=4)
        _button(row, "Продолжить", MUTED, self._close_confirm).pack(side="left", padx=4)
        w.update_idletasks()
        ind = self._indicator
        w.geometry("+%d+%d" % (ind.winfo_x() + ind.winfo_width() - w.winfo_width(), ind.winfo_y() + ind.winfo_height() + 6))
        _no_activate(w)
        w.after(8000, self._close_confirm)

    def _close_confirm(self):
        if self._confirm is not None:
            try:
                self._confirm.destroy()
            except tk.TclError:
                pass
            self._confirm = None

    def _do_stop(self):
        self._close_confirm()
        if self._on_stop:
            self._on_stop()
