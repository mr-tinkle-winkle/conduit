"""
conduit_eq.py -- the parametric equaliser every Conduit device (hub) can have.
Stdlib only: the daemon uses it to generate the PipeWire filter-chain config and
the live control values; the GUI uses the same maths to draw the response curve.

In state.json each hub (speaker, mic, every Custom Conduit) may carry:

    "eq": {"enabled": true, "preamp": -3.0,
           "bands": [{"type": "peaking", "freq": 1000.0, "q": 1.0, "gain": 4.0, "enabled": true}, ...]}

Types: peaking, lowshelf, highshelf (use gain), highpass, lowpass, notch (ignore gain).
A band may carry a "label" (what that frequency area is, e.g. "Presence").  "seeded" records that
the EQ window already gave this device its default points (voice or audio tuning).

How it runs: one `pipewire -c` helper per equalised hub (exactly like noise
suppression) with a chain of PipeWire's builtin biquads:

    hub output -> conduit_eq_<hub>_in  [ eq_pre -> eq_b1 -> eq_b2 ... ]  conduit_eq_<hub>_out -> consumers

eq_pre is the preamp (a high-shelf at 10 Hz, i.e. flat gain -- keeps the chain
all-biquad).  Frequency / Q / gain changes are pushed live with
`pw-cli set-param`; only adding, removing or retyping a band restarts the helper.
"""
from __future__ import annotations

import cmath
import math

TYPES = {
    "peaking": "bq_peaking", "lowshelf": "bq_lowshelf", "highshelf": "bq_highshelf",
    "highpass": "bq_highpass", "lowpass": "bq_lowpass", "notch": "bq_notch",
}
TYPE_LABELS = {"peaking": "Bell", "lowshelf": "Low shelf", "highshelf": "High shelf",
               "highpass": "High-pass", "lowpass": "Low-pass", "notch": "Notch"}
USES_GAIN = ("peaking", "lowshelf", "highshelf")
MAX_BANDS = 12
FREQ_MIN, FREQ_MAX = 20.0, 20000.0
GAIN_MIN, GAIN_MAX = -24.0, 24.0
Q_MIN, Q_MAX = 0.1, 18.0
SAMPLE_RATE = 48000.0

NODE_PREFIX = "conduit_eq_"


def default_eq() -> dict:
    return {"enabled": False, "preamp": 0.0, "bands": [], "seeded": False}


def _f(v, default, lo, hi):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    return max(lo, min(hi, v))


def norm_band(b) -> "dict | None":
    if not isinstance(b, dict) or b.get("type") not in TYPES:
        return None
    return {"type": b["type"], "freq": _f(b.get("freq"), 1000.0, FREQ_MIN, FREQ_MAX),
            "q": _f(b.get("q"), 1.0, Q_MIN, Q_MAX), "gain": _f(b.get("gain"), 0.0, GAIN_MIN, GAIN_MAX),
            "enabled": bool(b.get("enabled", True)),
            **({"label": str(b["label"])[:24]} if isinstance(b.get("label"), str) and b["label"].strip() else {})}


def norm_eq(raw) -> dict:
    eq = default_eq()
    if not isinstance(raw, dict):
        return eq
    eq["enabled"] = bool(raw.get("enabled", False))
    eq["preamp"] = _f(raw.get("preamp"), 0.0, GAIN_MIN, GAIN_MAX)
    eq["seeded"] = bool(raw.get("seeded", False))
    bands = [norm_band(b) for b in raw.get("bands", []) if True] if isinstance(raw.get("bands"), list) else []
    eq["bands"] = [b for b in bands if b][:MAX_BANDS]
    return eq


def node_names(key: str) -> tuple:
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in key)
    return f"{NODE_PREFIX}{safe}_in", f"{NODE_PREFIX}{safe}_out"


def active_bands(eq: dict) -> list:
    return [b for b in eq.get("bands", []) if b.get("enabled", True)]


def structure(eq: dict) -> tuple:
    """What needs a restart when it changes: the list of (enabled) band types."""
    return tuple(b["type"] for b in active_bands(eq))


def controls(eq: dict) -> dict:
    """{control name: value} for the running chain."""
    out = {"eq_pre:Freq": 10.0, "eq_pre:Q": 0.7071, "eq_pre:Gain": float(eq.get("preamp", 0.0))}
    for i, b in enumerate(active_bands(eq), 1):
        out[f"eq_b{i}:Freq"] = float(b["freq"])
        out[f"eq_b{i}:Q"] = float(b["q"])
        out[f"eq_b{i}:Gain"] = float(b["gain"]) if b["type"] in USES_GAIN else 0.0
    return out


def config_text(key: str, description: str, eq: dict, prereq_modules: str) -> str:
    in_name, out_name = node_names(key)
    description = description.replace('"', "'")
    ctl = controls(eq)
    names = ["eq_pre"] + [f"eq_b{i}" for i in range(1, len(active_bands(eq)) + 1)]
    labels = ["bq_highshelf"] + [TYPES[b["type"]] for b in active_bands(eq)]
    nodes = []
    for name, label in zip(names, labels):
        nodes.append(f'                    {{ type = builtin name = {name} label = {label} control = {{ '
                     f'"Freq" = {ctl[name + ":Freq"]:.3f} "Q" = {ctl[name + ":Q"]:.4f} "Gain" = {ctl[name + ":Gain"]:.3f} }} }}')
    links = [f'                    {{ output = "{a}:Out" input = "{b}:In" }}' for a, b in zip(names, names[1:])]
    return f"""
context.modules = [
{prereq_modules}    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = "Conduit EQ - {description}"
            filter.graph = {{
                nodes = [
{chr(10).join(nodes)}
                ]
                links = [
{chr(10).join(links)}
                ]
                inputs = [ "{names[0]}:In" ]
                outputs = [ "{names[-1]}:Out" ]
            }}
            capture.props = {{
                node.name = "{in_name}"
                media.class = Audio/Sink
                audio.position = [ FL FR ]
            }}
            playback.props = {{
                node.name = "{out_name}"
                media.class = Audio/Source
                audio.position = [ FL FR ]
            }}
        }}
    }}
]
"""


def set_param_arg(changes: dict) -> str:
    """The Props value for `pw-cli set-param <node> Props '<this>'`."""
    return "{ params = [ " + " ".join(f'"{k}" {v:.4f}' for k, v in changes.items()) + " ] }"


# ---------------------------------------------------------------------------
# response maths (RBJ cookbook biquads -- what PipeWire's builtin bq_* implement)
# ---------------------------------------------------------------------------

def biquad_coeffs(btype: str, freq: float, q: float, gain_db: float, fs: float = SAMPLE_RATE):
    w0 = 2 * math.pi * min(max(freq, 1.0), fs * 0.49) / fs
    cw, sw = math.cos(w0), math.sin(w0)
    q = max(q, 0.01)
    alpha = sw / (2 * q)
    A = 10 ** (gain_db / 40)
    if btype == "peaking":
        b = (1 + alpha * A, -2 * cw, 1 - alpha * A)
        a = (1 + alpha / A, -2 * cw, 1 - alpha / A)
    elif btype == "lowshelf":
        s = 2 * math.sqrt(A) * alpha
        b = (A * ((A + 1) - (A - 1) * cw + s), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - s))
        a = ((A + 1) + (A - 1) * cw + s, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - s)
    elif btype == "highshelf":
        s = 2 * math.sqrt(A) * alpha
        b = (A * ((A + 1) + (A - 1) * cw + s), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - s))
        a = ((A + 1) - (A - 1) * cw + s, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - s)
    elif btype == "highpass":
        b = ((1 + cw) / 2, -(1 + cw), (1 + cw) / 2)
        a = (1 + alpha, -2 * cw, 1 - alpha)
    elif btype == "lowpass":
        b = ((1 - cw) / 2, 1 - cw, (1 - cw) / 2)
        a = (1 + alpha, -2 * cw, 1 - alpha)
    elif btype == "notch":
        b = (1, -2 * cw, 1)
        a = (1 + alpha, -2 * cw, 1 - alpha)
    else:
        return (1, 0, 0), (1, 0, 0)
    return b, a


def band_db(band: dict, freq: float, fs: float = SAMPLE_RATE) -> float:
    b, a = biquad_coeffs(band["type"], band["freq"], band["q"], band.get("gain", 0.0), fs)
    z = cmath.exp(-1j * 2 * math.pi * freq / fs)
    num = b[0] + b[1] * z + b[2] * z * z
    den = a[0] + a[1] * z + a[2] * z * z
    mag = abs(num / den) if den != 0 else 1.0
    return 20 * math.log10(max(mag, 1e-6))


def response_db(eq: dict, freqs) -> list:
    bands = active_bands(eq)
    pre = float(eq.get("preamp", 0.0))
    return [pre + sum(band_db(b, f) for b in bands) for f in freqs]


def log_freqs(n: int, lo: float = FREQ_MIN, hi: float = FREQ_MAX) -> list:
    return [lo * (hi / lo) ** (i / (n - 1)) for i in range(n)]


def _b(t, f, g=0.0, q=0.9, label=None):
    b = {"type": t, "freq": float(f), "q": float(q), "gain": float(g), "enabled": True}
    if label:
        b["label"] = label
    return b


# Default points: flat (0 dB) handles parked on the areas you normally reach for, so tuning
# is "grab Presence and lift it" instead of "where was presence again?".
VOICE_POINTS = [_b("lowshelf", 100, 0, 0.7, "Boom"), _b("peaking", 250, 0, 1.2, "Mud"),
                _b("peaking", 500, 0, 1.4, "Boxy"), _b("peaking", 1000, 0, 1.4, "Nasal"),
                _b("peaking", 3000, 0, 1.0, "Presence"), _b("peaking", 6500, 0, 2.0, "Sibilance"),
                _b("highshelf", 10000, 0, 0.7, "Air")]
AUDIO_POINTS = [_b("peaking", 50, 0, 1.0, "Sub-bass"), _b("peaking", 120, 0, 1.0, "Bass"),
                _b("peaking", 300, 0, 1.2, "Low-mids"), _b("peaking", 1000, 0, 1.0, "Mids"),
                _b("peaking", 2500, 0, 1.2, "Upper mids"), _b("peaking", 5000, 0, 1.2, "Presence"),
                _b("highshelf", 10000, 0, 0.7, "Air")]


def default_points(voice: bool) -> list:
    return [dict(b) for b in (VOICE_POINTS if voice else AUDIO_POINTS)]


PRESETS = {
    "Voice tuning points": {"preamp": 0.0, "bands": VOICE_POINTS},
    "Audio tuning points": {"preamp": 0.0, "bands": AUDIO_POINTS},
    "Flat": {"preamp": 0.0, "bands": []},
    "Bass boost": {"preamp": -4.0, "bands": [_b("lowshelf", 110, 6.0, 0.7), _b("peaking", 60, 2.0, 1.0)]},
    "Bass cut": {"preamp": 0.0, "bands": [_b("highpass", 90, q=0.707), _b("lowshelf", 200, -3.0, 0.7)]},
    "Treble boost": {"preamp": -3.0, "bands": [_b("highshelf", 6000, 5.0, 0.7)]},
    "Vocal clarity": {"preamp": -2.0, "bands": [_b("highpass", 80, q=0.707), _b("peaking", 250, -3.0, 1.2),
                                                _b("peaking", 3200, 4.0, 1.0), _b("highshelf", 9000, 2.0, 0.7)]},
    "Podcast mic": {"preamp": -1.0, "bands": [_b("highpass", 75, q=0.707), _b("peaking", 180, 2.0, 1.0),
                                              _b("peaking", 400, -2.5, 1.4), _b("peaking", 4500, 3.0, 1.2),
                                              _b("lowpass", 15000, q=0.707)]},
    "Loudness": {"preamp": -5.0, "bands": [_b("lowshelf", 100, 6.0, 0.7), _b("peaking", 1000, -2.0, 0.6),
                                           _b("highshelf", 8000, 5.0, 0.7)]},
    "Headphones (warm)": {"preamp": -3.0, "bands": [_b("lowshelf", 120, 3.5, 0.7), _b("peaking", 3000, -2.5, 1.5),
                                                    _b("highshelf", 10000, -1.5, 0.7)]},
    "Laptop speakers": {"preamp": -4.0, "bands": [_b("highpass", 120, q=0.707), _b("peaking", 220, 4.0, 1.0),
                                                  _b("peaking", 2500, -2.0, 1.0), _b("highshelf", 8000, 3.0, 0.7)]},
    "Telephone": {"preamp": 0.0, "bands": [_b("highpass", 300, q=0.9), _b("lowpass", 3400, q=0.9),
                                           _b("peaking", 1800, 4.0, 1.0)]},
}
