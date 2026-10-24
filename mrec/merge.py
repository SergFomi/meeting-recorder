"""Слияние расшифровок двух потоков в одну и удаление эха.

Сегмент — кортеж (start_sec, end_sec, text). Поток «mic» — вы, «system» — собеседник.
Эхо: микрофон иногда ловит голос клиента из колонок. Реплику микрофона убираем только если
она по тексту и по времени почти совпадает с репликой клиента. Короткие реплики («да», «понял»,
«угу») не удаляем никогда: потерять вашу реплику хуже, чем оставить лишнюю.
"""
import os
import re
from difflib import SequenceMatcher

# подписи задаются настройками speaker_me / speaker_them (приложение передаёт их через окружение)
ME = os.environ.get("MREC_ME") or "Я"
THEM = os.environ.get("MREC_THEM") or "Собеседник"
MIN_WORDS_FOR_ECHO = 3
ECHO_SIMILARITY = 0.7
ECHO_TIME_SLACK = 2.0     # эхо приходит с задержкой и обрезками, сек


def _norm(text):
    return re.sub(r"[^\w\s]", "", text.lower().replace("ё", "е")).split()


def _similar(a_words, b_words):
    return SequenceMatcher(None, a_words, b_words).ratio()


def drop_echo(mic, system):
    """Вернуть реплики микрофона без тех, что являются эхом реплик клиента."""
    kept = []
    for s, e, text in mic:
        words = _norm(text)
        if len(words) < MIN_WORDS_FOR_ECHO:
            kept.append((s, e, text))
            continue
        is_echo = False
        for s2, e2, text2 in system:
            if s2 > e + ECHO_TIME_SLACK or e2 < s - ECHO_TIME_SLACK:
                continue
            w2 = _norm(text2)
            if len(w2) >= MIN_WORDS_FOR_ECHO and _similar(words, w2) >= ECHO_SIMILARITY:
                is_echo = True
                break
        if not is_echo:
            kept.append((s, e, text))
    return kept


def stamp(seconds):
    seconds = int(seconds)
    return "%02d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def build_transcript(mic, system, header=None):
    """Собрать итоговый текст: необязательные строки шапки + реплики по времени."""
    lines = [(s, ME, t) for s, _e, t in drop_echo(mic, system)]
    lines += [(s, THEM, t) for s, _e, t in system]
    lines.sort(key=lambda x: x[0])
    out = ["# " + h for h in (header or [])]
    out += ["[%s] %s: %s" % (stamp(s), who, t) for s, who, t in lines]
    return "\n".join(out) + "\n"
