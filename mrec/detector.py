"""Определение звонка: какие программы прямо сейчас держат микрофон (реестр Windows)."""
import ctypes
import os
import threading
import winreg
from ctypes import wintypes

_BASE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


_com = threading.local()


def _active_sessions(flow):
    """Процессы (нижний регистр) с активной аудиосессией: flow=1 запись с микрофона, flow=0 воспроизведение.

    Это надёжнее реестра: Telegram в живом звонке в реестр «микрофон занят» не попадал,
    а в списке сессий захвата он виден (проверено на звонке 22.10.2026).
    """
    import comtypes
    import psutil
    from comtypes import CLSCTX_ALL
    from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.constants import CLSID_MMDeviceEnumerator

    if not getattr(_com, "ready", False):
        comtypes.CoInitialize()
        _com.ready = True
    enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    found = set()
    devices = enum.EnumAudioEndpoints(flow, 1)       # DEVICE_STATE_ACTIVE
    for d in range(devices.GetCount()):
        mgr = devices.Item(d).Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)
        sessions = mgr.GetSessionEnumerator()
        for i in range(sessions.GetCount()):
            ctl = sessions.GetSession(i).QueryInterface(IAudioSessionControl2)
            pid = ctl.GetProcessId()
            if ctl.GetState() == 1 and pid:           # AudioSessionStateActive
                try:
                    found.add(psutil.Process(pid).name().lower())
                except psutil.Error:
                    pass
    return found


def apps_using_microphone():
    """Имена процессов, которые прямо сейчас пишут с микрофона. Реестр нужен только как запасной путь."""
    try:
        return _active_sessions(1)
    except Exception:
        return _registry_mic_users()


def apps_in_call():
    """Программы, у которых одновременно активны запись с микрофона и воспроизведение.

    Звонок двусторонний: программа и слышит собеседника, и пишет голос. Голосовое сообщение,
    диктовка и голосовой поиск пишут с микрофона, но ничего не воспроизводят, поэтому плашку
    не вызывают.
    """
    try:
        return _active_sessions(1) & _active_sessions(0)
    except Exception:
        return _registry_mic_users()


def _registry_mic_users():
    """Запасной путь: у кого в реестре LastUsedTimeStop == 0. Может залипать, если программа упала."""
    found = set()
    for sub in (_BASE, _BASE + r"\NonPackaged"):
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, name) as k:
                        stop, _ = winreg.QueryValueEx(k, "LastUsedTimeStop")
                        start, _ = winreg.QueryValueEx(k, "LastUsedTimeStart")
                except OSError:
                    continue
                if start and stop == 0:
                    found.add(os.path.basename(name.replace("#", "\\")).lower())
    return found


def window_titles_of(process_names):
    """Заголовки видимых окон процессов с указанными именами (нижний регистр)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    titles = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def exe_of(hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(520)
            size = wintypes.DWORD(520)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            kernel32.CloseHandle(h)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if exe_of(hwnd) in process_names:
                    titles.append(buf.value.lower())
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return titles


def browser_label(titles, title_labels):
    """Как назвать звонок в браузере: по слову из заголовка окна (Max, Телемост, Meet), иначе «Browser»."""
    for title in titles:
        for word, label in title_labels.items():
            if word in title:
                return label
    return "Browser"


def active_call_programs(cfg, in_use=None):
    """Названия звонковых программ, которые сейчас держат микрофон (для имени файла и плашки).

    Браузер считается звонком, как только он пишет с микрофона: Max работает только в браузере
    (десктопного приложения нет), заголовок вкладки не всегда виден, поэтому слово из заголовка
    нужно только для красивого имени файла.
    """
    in_use = apps_in_call() if in_use is None else in_use
    names = [label for exe, label in cfg["call_apps"].items() if exe in in_use]
    browsers = [b for b in cfg["browser_apps"] if b in in_use]
    if browsers:
        names.append(browser_label(window_titles_of(set(browsers)), cfg["browser_title_labels"]))
    return names


class CallTracker:
    """Чистая машина состояний (без реестра и времени системы, поэтому легко тестируется).

    update(now, programs) возвращает "started" один раз, когда звонковая программа держит микрофон
    не меньше min_in_use секунд, и "ended", когда микрофон свободен quiet секунд.
    Пока звонок не закончился, повторных "started" нет: так закрытая плашка не появляется снова.
    """

    def __init__(self, min_in_use=5, quiet=30):
        self.min_in_use = min_in_use
        self.quiet = quiet
        self._since = None      # с какого момента непрерывно занят микрофон
        self._free_since = None
        self.active = False
        self.programs = []

    def update(self, now, programs, min_in_use=None):
        """min_in_use: сколько секунд ждать для этого случая (для звонков в браузере дольше)."""
        if programs:
            self._free_since = None
            if self._since is None:
                self._since = now
            self.programs = list(programs)
            if not self.active and now - self._since >= (min_in_use or self.min_in_use):
                self.active = True
                return "started"
            return None
        self._since = None
        if self.active:
            if self._free_since is None:
                self._free_since = now
            if now - self._free_since >= self.quiet:
                self.active = False
                self._free_since = None
                return "ended"
        return None
