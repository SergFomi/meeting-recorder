"""Доставка расшифровки: scp на сервер агента и отправка файла в топик ботом Гермеса (скрипт tg-doc).

Токен бота остаётся на сервере. Команда ssh содержит только ASCII: подпись едет отдельным файлом.
"""
import logging
import shlex
import subprocess
from pathlib import Path

log = logging.getLogger("mrec.deliver")
NO_WINDOW = 0x08000000


def _text(raw):
    """Вывод ssh/scp на русской Windows бывает в кодировке консоли (cp866), а не в UTF-8."""
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return raw.decode("cp866", "replace").strip()


def _run(args, cwd, timeout):
    return subprocess.run(args, cwd=str(cwd), capture_output=True, timeout=timeout,
                          creationflags=NO_WINDOW)


def caption_for(meta, mention=""):
    """Подпись к файлу. Упоминание нужно, чтобы агент (бот Sonnet) принял сообщение от другого бота."""
    dur = int(meta.get("duration", 0))
    text = "Встреча %s · %s · %d мин %02d с" % (
        meta.get("started_at", "").replace("T", " ")[:16], meta.get("program", "?"), dur // 60, dur % 60)
    return (mention + " " + text) if mention else text


def save_local(job, folder):
    """Положить готовую расшифровку в обычную папку (если сервер не настроен)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (job.name + ".txt")).write_bytes((job.dir / "transcript.txt").read_bytes())


def deliver(job, server, local_dir=None):
    """Бросает RuntimeError с коротким описанием, если доставить не удалось."""
    if local_dir:
        save_local(job, local_dir)
    if not server.get("ssh_host"):
        return
    meta = job.meta
    name = job.name
    txt, cap = job.dir / (name + ".txt"), job.dir / (name + ".cap")
    txt.write_bytes((job.dir / "transcript.txt").read_bytes())
    remote_path = "~/%s/%s.txt" % (server["remote_dir"], name)
    cap.write_text(caption_for(meta, server.get("agent_mention", "")) + "\n" + remote_path, encoding="utf-8")
    host = server["ssh_host"]
    opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    try:
        r = _run(["scp", *opts, txt.name, cap.name, "%s:%s/" % (host, server["remote_dir"])], job.dir, 120)
        if r.returncode:
            raise RuntimeError("scp: " + _text(r.stderr)[:200])
        remote_cap = "~/%s/%s.cap" % (server["remote_dir"], name)
        cmd = "%s %s %s \"$(cat %s)\"" % (server["tg_doc"], shlex.quote(str(server["thread_id"])), remote_path, remote_cap)
        r = _run(["ssh", *opts, host, cmd], job.dir, 120)
        out = _text(r.stdout)
        if r.returncode or not out.endswith("ok"):
            raise RuntimeError("tg-doc: " + (out or _text(r.stderr))[:200])
    except subprocess.TimeoutExpired:
        raise RuntimeError("сервер не ответил вовремя")
    finally:
        txt.unlink(missing_ok=True)
        cap.unlink(missing_ok=True)
