import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrec import aec  # noqa: E402

RATE = 16000


def _speechlike(seconds, seed):
    """Шум с «слоговой» огибающей: есть паузы и всплески, как у речи."""
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    env = np.repeat(rng.random(int(seconds * 4) + 1) > 0.4, RATE // 4)[:n].astype(np.float32)
    return (rng.standard_normal(n).astype(np.float32) * 0.1 * env)


def _db(a, b):
    return 10 * np.log10((np.mean(a ** 2) + 1e-12) / (np.mean(b ** 2) + 1e-12))


def test_echo_of_client_is_removed_and_own_voice_survives():
    seconds = 40
    client = _speechlike(seconds, seed=1)
    own = _speechlike(seconds, seed=2)
    delay = 900                                   # ~56 мс
    path = np.zeros(1200, np.float32)
    path[delay - 800] = 0.5
    path[delay - 800 + 150] = 0.2                 # простая «комната»
    echo = np.convolve(client, path)[:len(client)]
    echo = np.concatenate([np.zeros(800, np.float32), echo])[:len(client)]
    mic = own + echo

    clean = aec.cancel_echo_stft(mic, client)

    only_echo = np.abs(client) > 0.02             # собеседник говорит, вы молчите -> остаётся только эхо
    only_echo &= np.abs(own) < 1e-4
    assert _db(mic[only_echo], clean[only_echo]) > 10
    assert len(clean) == len(mic)
    own_only = np.abs(client) < 1e-6
    own_only &= np.abs(own) > 0.02
    assert abs(_db(mic[own_only], clean[own_only])) < 1.5   # голос Сергея в паузах клиента не тронут


def test_short_input_is_returned_unchanged():
    x = np.ones(100, np.float32)
    assert np.array_equal(aec.cancel_echo_stft(x, x), x)
