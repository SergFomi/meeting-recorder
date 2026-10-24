import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrec.capture import Resampler, TimelineWriter, TARGET_RATE  # noqa: E402
from mrec.detector import CallTracker, browser_label  # noqa: E402
from mrec import merge  # noqa: E402
from mrec.merge import build_transcript, drop_echo  # noqa: E402


# ---- слияние и эхо ----

def test_echo_of_client_is_dropped_from_mic():
    mic = [(10.0, 12.0, "Слышно, но плохо, блин"), (13.0, 14.0, "Да, понял")]
    system = [(10.4, 12.2, "Слышно но плохо блин")]
    assert drop_echo(mic, system) == [(13.0, 14.0, "Да, понял")]


def test_short_replies_are_never_dropped():
    mic = [(5.0, 5.5, "Да")]
    system = [(5.0, 5.5, "Да")]
    assert drop_echo(mic, system) == mic


def test_similar_text_far_in_time_is_kept():
    mic = [(100.0, 102.0, "Слышно но плохо блин")]
    system = [(10.0, 12.0, "Слышно но плохо блин")]
    assert drop_echo(mic, system) == mic


def test_transcript_is_sorted_and_labelled():
    text = build_transcript([(3.0, 4.0, "Привет, как дела у вас")], [(1.0, 2.0, "Алло")], header=["Встреча"])
    assert text.splitlines() == ["# Встреча", "[00:00:01] %s: Алло" % merge.THEM, "[00:00:03] %s: Привет, как дела у вас" % merge.ME]


def test_browser_call_is_named_by_title_or_generic():
    labels = {"max": "Max", "телемост": "Telemost-web"}
    assert browser_label(["входящие - gmail - google chrome", "max - google chrome"], labels) == "Max"
    assert browser_label(["Телемост - Яндекс".lower()], labels) == "Telemost-web"
    assert browser_label(["входящие - gmail"], labels) == "Browser"
    assert browser_label([], labels) == "Browser"


# ---- машина состояний звонка ----

def test_call_starts_after_min_in_use_and_only_once():
    t = CallTracker(min_in_use=5, quiet=30)
    assert t.update(0, ["Telegram"]) is None
    assert t.update(4, ["Telegram"]) is None
    assert t.update(5, ["Telegram"]) == "started"
    assert t.update(6, ["Telegram"]) is None


def test_browser_calls_wait_longer_before_the_plaque():
    t = CallTracker(min_in_use=5, quiet=30)
    assert t.update(0, ["Browser"], min_in_use=10) is None
    assert t.update(7, ["Browser"], min_in_use=10) is None
    assert t.update(10, ["Browser"], min_in_use=10) == "started"


def test_short_microphone_use_is_ignored():
    t = CallTracker(min_in_use=5, quiet=30)
    t.update(0, ["Telegram"])
    t.update(2, [])
    assert t.update(10, ["Telegram"]) is None


def test_call_ends_after_quiet_period_and_next_call_starts_again():
    t = CallTracker(min_in_use=5, quiet=30)
    t.update(0, ["MicroSIP"])
    assert t.update(5, ["MicroSIP"]) == "started"
    assert t.update(10, []) is None
    assert t.update(39, []) is None
    assert t.update(40, []) == "ended"
    t.update(50, ["MicroSIP"])
    assert t.update(55, ["MicroSIP"]) == "started"


def test_short_gap_during_call_is_not_a_new_call():
    t = CallTracker(min_in_use=5, quiet=30)
    t.update(0, ["Telegram"])
    t.update(5, ["Telegram"])
    t.update(10, [])
    t.update(20, ["Telegram"])
    assert t.update(40, ["Telegram"]) is None


# ---- запись ----

def _tone(rate, seconds, freq=440.0):
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * freq * t) * 12000).astype(np.float32)


def _dominant_freq(samples, rate):
    spec = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return np.argmax(spec) * rate / len(samples)


def test_resampler_keeps_frequency_and_length_for_48k_and_44k():
    for rate in (48000, 44100):
        r = Resampler(rate)
        x = _tone(rate, 2.0)
        out = np.concatenate([r.process(x[i:i + 1024]) for i in range(0, len(x), 1024)])
        assert abs(len(out) - 2 * TARGET_RATE) < 0.1 * TARGET_RATE
        assert abs(_dominant_freq(out[2000:].astype(np.float32), TARGET_RATE) - 440) < 15


def test_timeline_writer_pads_gaps_with_silence(tmp_path):
    now = [0.0]
    w = TimelineWriter(tmp_path / "a.wav", t0=0.0, clock=lambda: now[0])
    chunk = np.ones(1600, np.int16)           # 0.1 с
    now[0] = 0.1
    w.feed(chunk)
    now[0] = 5.1                              # 5 секунд ничего не приходило
    w.feed(chunk)
    now[0] = 6.0
    dur = w.close()
    assert abs(dur - 6.0) < 0.01
    with wave.open(str(tmp_path / "a.wav"), "rb") as f:
        data = np.frombuffer(f.readframes(f.getnframes()), np.int16)
    assert len(data) == 6 * TARGET_RATE
    assert data[:1600].all() and not data[1600:1600 + 4 * TARGET_RATE].any()
