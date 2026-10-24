"""Настройки рекордера. Пользовательские значения лежат в settings.json рядом с данными."""
import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MeetingRecorder"
RECORDINGS_DIR = DATA_DIR / "recordings"
LOG_PATH = DATA_DIR / "recorder.log"
SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULTS = {
    # Звонковые программы: имя процесса (нижний регистр) -> как называть в имени файла.
    "call_apps": {
        "telegram.exe": "Telegram",
        "yandextelemost.exe": "Telemost",
        "zoom.exe": "Zoom",
        "microsip.exe": "MicroSIP",
        "max.exe": "Max",
    },
    # Звонок в браузере (Max работает только так): браузер пишет с микрофона. Слово в заголовке окна
    # нужно только для названия в имени файла, иначе «Browser».
    "browser_apps": ["chrome.exe", "msedge.exe", "yandex.exe", "browser.exe", "firefox.exe"],
    "browser_title_labels": {"max": "Max", "макс": "Max", "телемост": "Telemost-web", "telemost": "Telemost-web",
                             "google meet": "Meet", "meet.google": "Meet"},
    "browser_min_in_use_sec": 10,  # в браузере ждём дольше: короткие всплески микрофона звонком не считаем
    "min_in_use_sec": 5,          # короткие занятия микрофона (уведомления) игнорируем
    "call_end_quiet_sec": 30,     # столько микрофон должен быть свободен, чтобы звонок считался новым
    "plaque_timeout_sec": 60,     # плашка сама гаснет, если на неё не ответили
    # Подписи реплик в расшифровке: микрофон (вы) и звук из колонок (собеседник).
    "speaker_me": "Я",
    "speaker_them": "Собеседник",
    # Куда класть готовые расшифровки.
    "transcripts_dir": str(Path.home() / "Documents" / "Расшифровки встреч"),
    # Необязательная отправка на свой сервер по ssh с пересылкой файла в Telegram (см. server/README.md).
    # Пока ssh_host пустой, расшифровка просто лежит в transcripts_dir.
    "server": {
        "ssh_host": "",           # имя из ~/.ssh/config
        "remote_dir": "meetings",
        "thread_id": "",          # топик (тема) Telegram-группы
        "agent_mention": "",      # @бота, если файл должен обработать другой бот
        "tg_doc": "~/bin/tg-doc",
    },
    # Где считать GigaAM: "cuda" (с откатом на процессор, если видеокарта недоступна) или "cpu".
    # По скорости разницы нет (20 минут записи: 112 с на видеокарте, 117 с на процессоре),
    # но видеокарта разгружает процессор, он нужен звонку.
    "asr_device": "cuda",
    "keep_audio_days": 14,
    "retry_every_sec": 600,
}


def load():
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        user = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg
