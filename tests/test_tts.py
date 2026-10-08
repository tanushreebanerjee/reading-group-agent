import threading
import time

import numpy as np

from audio.tts import Speaker, TTS, sentences, speakable


def test_speakable_expands_citations():
    assert speakable("See §4.3, p.7 and Fig. 2 (App. D.2).") == "See section 4.3, page 7 and figure 2 (appendix D.2)."
    assert speakable("lr 5×10^-5 vs. 2×10^-4") == "lr 5 times ten to the -5 versus 2 times ten to the -4"
    assert speakable("M1→0") == "M1 to 0"


def test_sentences_keep_decimals_and_tables():
    s = sentences("GLD gets 16.362 on Re10K (Table 3). The VAE gets 15.656. Done!")
    assert s == ["GLD gets 16.362 on Re10K (Table 3).", "The VAE gets 15.656.", "Done!"]


class SlowTTS(TTS):
    def __init__(self):
        super().__init__({})
        self.said = []

    def synth(self, text):
        self.said.append(text)
        return np.zeros(100, dtype=np.float32), 24000


def test_speaker_plays_sentences_and_signals_start_end():
    events, played = [], []
    sp = Speaker(SlowTTS(), None, on_start=lambda: events.append("start"), on_end=lambda: events.append("end"),
                 play=lambda x, sr: played.append(len(x)))
    sp.say("One. Two. Three.")
    sp.thread.join(2)
    assert played == [100, 100, 100] and events == ["start", "end"] and not sp.speaking


def test_stop_cuts_off_speech():
    gate = threading.Event()
    played = []

    def play(x, sr):
        played.append(1)
        gate.wait(1)  # block like a long sentence

    sp = Speaker(SlowTTS(), None, play=play)
    sp.say("One. Two. Three. Four.")
    time.sleep(0.1)
    sp._stop.set(); gate.set()
    sp.stop()
    assert not sp.speaking and len(played) < 4


def test_long_first_sentence_split_at_comma():
    s = sentences("On RealEstate10K, GLD reports 16.362 PSNR, compared with 15.656 for the VAE baseline (Table 3). Next.")
    assert s[0] == "On RealEstate10K, GLD reports 16.362 PSNR," and s[-1] == "Next."
