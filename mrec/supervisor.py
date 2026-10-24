"""Сторож: запускает программу и поднимает её заново, если она пропала без команды «Выход».

Запуск: pythonw -m mrec.supervisor (так стоит в автозагрузке и на ярлыке рабочего стола).
Код возврата 0 (обычный выход из меню трея или уже запущенная копия) значит «не перезапускать».
"""
import logging
import subprocess
import sys
import time

from . import config

NO_WINDOW = 0x08000000
MAX_RESTARTS_PER_HOUR = 20


def main():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(config.LOG_PATH), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s supervisor: %(message)s")
    starts = []
    while True:
        starts = [t for t in starts if time.time() - t < 3600]
        if len(starts) >= MAX_RESTARTS_PER_HOUR:
            logging.error("слишком много перезапусков за час, сторож останавливается")
            return
        starts.append(time.time())
        code = subprocess.call([sys.executable, "-m", "mrec"], creationflags=NO_WINDOW)
        if code == 0:
            return
        logging.warning("программа завершилась с кодом %s, перезапуск через 5 секунд", code)
        time.sleep(5)


if __name__ == "__main__":
    main()
