"""Удаление эха из дорожки микрофона.

Когда вы говорите не в наушниках, микрофон слышит голос собеседника из колонок. Звук клиента у нас
есть отдельно (дорожка system), поэтому эхо можно вычесть: адаптивный фильтр в частотной области
(блоковый NLMS с разбиением на отрезки, PBFDAF) учится предсказывать по дорожке клиента то, что
микрофон слышит из колонок, и вычитает это. Работает по готовым дорожкам после звонка.
"""
import numpy as np

RATE = 16000


def _delay_estimate(mic, ref, max_ms=400):
    """Грубая оценка задержки эха (в отсчётах) по кросс-корреляции."""
    n = min(len(mic), len(ref), RATE * 60)       # минуты хватает
    m, r = mic[:n], ref[:n]
    size = 1 << int(np.ceil(np.log2(2 * n)))
    c = np.fft.irfft(np.fft.rfft(m, size) * np.conj(np.fft.rfft(r, size)), size)
    maxlag = int(max_ms * RATE / 1000)
    window = np.concatenate([c[-maxlag:], c[:maxlag]])
    return int(np.argmax(np.abs(window))) - maxlag


def cancel_echo_stft(mic, ref, n_fft=4096, hop=1024, window_frames=300, floor=1e-4):
    """Вычитание эха фильтром Винера в частотной области, подобранным по скользящему окну кадров.

    Для каждой частоты H = sum(M * conj(R)) / sum(|R|^2) по ближайшим window_frames кадрам в обе
    стороны (по умолчанию ~20 с). Речь самого Сергея с опорой не связана и при усреднении
    «вымывается», так что фильтр описывает только путь колонки -> микрофон. Работает по готовым
    дорожкам, поэтому может смотреть и вперёд.
    """
    n = min(len(mic), len(ref))
    if n < n_fft * 2:
        return mic[:n].copy()
    mic, ref = mic[:n].astype(np.float32), ref[:n].astype(np.float32)
    delay = max(0, _delay_estimate(mic, ref))
    if delay:
        ref = np.concatenate([np.zeros(delay, np.float32), ref])[:n]
    win = np.hanning(n_fft).astype(np.float32)
    pad = n_fft
    total = ((n + 2 * pad - n_fft) // hop + 1) * hop + n_fft
    mp = np.pad(mic, (pad, total - n - pad))
    rp = np.pad(ref, (pad, total - n - pad))
    frames = (len(mp) - n_fft) // hop + 1
    idx = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
    m = np.fft.rfft(mp[idx] * win, axis=1)
    r = np.fft.rfft(rp[idx] * win, axis=1)

    cross = np.concatenate([np.zeros((1, m.shape[1]), np.complex64), np.cumsum(m * np.conj(r), axis=0)])
    power = np.concatenate([np.zeros((1, m.shape[1])), np.cumsum(np.abs(r) ** 2, axis=0)])
    lo = np.clip(np.arange(frames) - window_frames, 0, frames)
    hi = np.clip(np.arange(frames) + window_frames + 1, 0, frames)
    c = cross[hi] - cross[lo]
    p = power[hi] - power[lo]
    h = c / (p + floor * (p.mean(axis=0, keepdims=True) + 1e-9) + 1e-12)
    e = m - h * r

    frames_t = np.fft.irfft(e, n_fft, axis=1) * win
    out = np.zeros(len(mp), np.float32)
    norm = np.zeros(len(mp), np.float32)
    for i in range(frames):
        s = i * hop
        out[s:s + n_fft] += frames_t[i]
        norm[s:s + n_fft] += win * win
    out = out / np.maximum(norm, 1e-6)
    return out[pad:pad + n]


def cancel_echo(mic, ref, block=256, parts=24, mu=0.35, geigel=None):
    """Вернуть дорожку микрофона без эха ref. mic и ref: float32 в диапазоне -1..1, 16 кГц.

    parts*block отсчётов = длина «хвоста» эха (24*256 = 6144 = 384 мс).
    geigel: если задан, адаптация замораживается, когда пик микрофона больше geigel * пик опоры
    за последние ~0.3 с, то есть когда, скорее всего, говорите вы сами.
    """
    n = min(len(mic), len(ref))
    if n < block * 4:
        return mic[:n].copy()
    mic, ref = mic[:n].astype(np.float32), ref[:n].astype(np.float32)
    delay = max(0, _delay_estimate(mic, ref))
    if delay:
        ref = np.concatenate([np.zeros(delay, np.float32), ref])[:n]   # выровнять эхо с опорой
    total = int(np.ceil(n / block)) * block
    mic = np.pad(mic, (0, total - n))
    ref = np.pad(ref, (0, total - n))

    ref_peak = np.abs(ref)
    bins = block + 1
    w = np.zeros((parts, bins), np.complex64)
    hist = np.zeros((parts, bins), np.complex64)
    power = np.full(bins, 1e-3, np.float32)
    prev = np.zeros(block, np.float32)
    out = np.zeros(total, np.float32)

    for i in range(0, total, block):
        cur = ref[i:i + block]
        x = np.fft.rfft(np.concatenate([prev, cur]))
        prev = cur
        hist = np.roll(hist, 1, axis=0)
        hist[0] = x
        echo = np.fft.irfft((w * hist).sum(axis=0))[block:]
        d = mic[i:i + block]
        e = d - echo
        out[i:i + block] = e

        power = 0.9 * power + 0.1 * (np.abs(x) ** 2).astype(np.float32)
        ref_energy = float(np.dot(cur, cur))
        if ref_energy < 1e-4 * block:                      # клиент молчит: учиться нечему
            continue
        if geigel is not None and np.abs(d).max() > geigel * ref_peak[max(0, i - 4 * block):i + block].max():
            continue                                        # говорите вы: не адаптироваться (детектор Гейгеля)
        grad = np.conj(hist) * np.fft.rfft(np.concatenate([np.zeros(block, np.float32), e])) / (power * parts + 1e-6)
        upd = w + mu * grad
        # ограничение: фильтр должен быть причинным, поэтому вторую половину импульсной характеристики обнуляем
        td = np.fft.irfft(upd, axis=1)
        td[:, block:] = 0
        w = np.fft.rfft(td, axis=1).astype(np.complex64)
    return out[:n]
