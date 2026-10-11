"""
A tiny in-memory PipeWire stand-in for tests: produces `pw-dump`-shaped JSON and
understands the handful of commands the daemon runs (pw-link, pw-link -d,
pw-cli create-node / destroy, wpctl set-volume / set-mute / set-default).
Install with ``FakePipeWire.install(monkey_modules)``.
"""
from __future__ import annotations

import json
import re
import subprocess


class _Result:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class FakePipeWire:
    def __init__(self):
        self._next = 100
        self.nodes = {}        # id -> dict(props, state)
        self.ports = {}        # id -> dict(node, dir, channel)
        self.links = {}        # id -> (out, in)
        self.volumes = {}
        self.mutes = {}
        self.defaults = []
        self.log = []

    def _id(self):
        self._next += 1
        return self._next

    def add_node(self, name, description=None, media_class="Audio/Sink", app=None, ins=(), outs=(),
                 state="running", client=None):
        nid = self._id()
        props = {"node.name": name, "node.description": description or name, "media.class": media_class}
        if app:
            props["application.name"] = app
        if client is not None:
            props["client.id"] = client
        self.nodes[nid] = {"props": props, "state": state}
        for ch in ins:
            self.ports[self._id()] = {"node": nid, "dir": "input", "channel": ch}
        for ch in outs:
            self.ports[self._id()] = {"node": nid, "dir": "output", "channel": ch}
        return nid

    def remove_node(self, nid):
        self.nodes.pop(nid, None)
        dead = {p for p, v in self.ports.items() if v["node"] == nid}
        for p in dead:
            self.ports.pop(p)
        self.links = {k: v for k, v in self.links.items() if v[0] not in dead and v[1] not in dead}

    def port(self, nid, direction, channel):
        for pid, p in self.ports.items():
            if p["node"] == nid and p["dir"] == direction and p["channel"] == channel:
                return pid
        raise KeyError((nid, direction, channel))

    def link(self, o, i):
        if (o, i) not in self.links.values():
            self.links[self._id()] = (o, i)

    def node_by_name(self, name):
        for nid, n in self.nodes.items():
            if n["props"]["node.name"] == name:
                return nid
        return None

    def link_set(self):
        return set(self.links.values())

    def dump(self):
        out = []
        for nid, n in self.nodes.items():
            out.append({"id": nid, "type": "PipeWire:Interface:Node", "info": {"props": n["props"], "state": n["state"]}})
        for pid, p in self.ports.items():
            out.append({"id": pid, "type": "PipeWire:Interface:Port",
                        "info": {"direction": p["dir"], "props": {"node.id": p["node"], "audio.channel": p["channel"]}}})
        for lid, (o, i) in self.links.items():
            out.append({"id": lid, "type": "PipeWire:Interface:Link",
                        "info": {"props": {"link.output.port": o, "link.input.port": i}}})
        return out

    # -- command emulation
    def run(self, cmd, *a, **kw):
        self.log.append(cmd)
        if cmd[0] == "pw-dump":
            return _Result(out=json.dumps(self.dump()))
        if cmd[0] == "pw-link":
            if cmd[1] == "-d":
                o, i = int(cmd[2]), int(cmd[3])
                self.links = {k: v for k, v in self.links.items() if v != (o, i)}
            else:
                o, i = int(cmd[1]), int(cmd[2])
                if o in self.ports and i in self.ports:
                    self.link(o, i)
                else:
                    return _Result(1, err="no such port")
            return _Result()
        if cmd[0] == "pw-cli":
            if cmd[1] == "create-node":
                args = cmd[3]
                name = re.search(r'node.name="([^"]+)"', args).group(1)
                desc = re.search(r'node.description="([^"]*)"', args).group(1)
                mc = re.search(r"media.class=(\S+)", args).group(1)
                pos = re.search(r"audio.position=\[([^\]]*)\]", args).group(1).split(",")
                outs = pos  # a null sink's monitor / a source's capture
                self.add_node(name, desc, mc, ins=pos, outs=outs, state="idle")
            elif cmd[1] == "destroy":
                self.remove_node(int(cmd[2]))
            return _Result()
        if cmd[0] == "wpctl":
            if cmd[1] == "set-volume":
                self.volumes[int(cmd[2])] = float(cmd[3])
            elif cmd[1] == "set-mute":
                self.mutes[int(cmd[2])] = cmd[3] == "1"
            elif cmd[1] == "set-default":
                self.defaults.append(int(cmd[2]))
            return _Result()
        if cmd[0] == "systemctl":
            return _Result()
        return _Result(127, err="unknown")

    def install(self, *modules):
        for m in modules:
            m.subprocess.run = self.run          # modules import subprocess at top level
        return self


def standard_graph():
    """A graph like the one in the handoff screenshots."""
    pw = FakePipeWire()
    ids = {}
    ids["vspk"] = pw.add_node("conduit_virtual_speaker", "Conduit Virtual Speaker", "Audio/Sink",
                              ins=("FL", "FR"), outs=("FL", "FR"))
    ids["vmic"] = pw.add_node("conduit_virtual_mic", "Conduit Virtual Mic", "Audio/Source/Virtual",
                              ins=("FL", "FR"), outs=("FL", "FR"))
    ids["ryzen_sink"] = pw.add_node("alsa_output.ryzen", "Ryzen HD Audio Controller Analog Stereo", "Audio/Sink",
                                    ins=("FL", "FR"), outs=("FL", "FR"))
    ids["ryzen_src"] = pw.add_node("alsa_input.ryzen", "Ryzen HD Audio Controller Analog Stereo", "Audio/Source",
                                   outs=("FL", "FR"), state="suspended")
    ids["p4"] = pw.add_node("alsa_input.p4next", "P4next Analog Stereo", "Audio/Source", outs=("FL", "FR"))
    ids["yeti"] = pw.add_node("alsa_input.yeti", "Blue Yeti", "Audio/Source", outs=("MONO",))
    ids["spotify"] = pw.add_node("spotify", "spotify", "Stream/Output/Audio", app="spotify",
                                 outs=("FL", "FR"), client=40)
    ids["chromium"] = pw.add_node("Chromium", "Chromium", "Stream/Output/Audio", app="Chromium",
                                  outs=("FL", "FR"), client=41)
    ids["chromium_in"] = pw.add_node("Chromium input", "Chromium input", "Stream/Input/Audio", app="Chromium input",
                                     ins=("FL", "FR"), client=41)
    ids["obs"] = pw.add_node("OBS", "OBS (Desktop Audio)", "Stream/Input/Audio", app="OBS",
                             ins=("FL", "FR"), state="idle")
    ids["vencord"] = pw.add_node("vencord-screen-share", "vencord-screen-share", "Audio/Sink",
                                 ins=("FL", "FR"), outs=("FL", "FR"), state="idle")
    # what WirePlumber does by itself: apps -> default sink, default source -> recorders
    for app in ("spotify", "chromium"):
        for ch in ("FL", "FR"):
            pw.link(pw.port(ids[app], "output", ch), pw.port(ids["vspk"], "input", ch))
    for ch in ("FL", "FR"):
        pw.link(pw.port(ids["vmic"], "output", ch), pw.port(ids["obs"], "input", ch))
    return pw, ids


V1_README_STATE = {
    "mic": {
        "inputs": [{"label": "Blue Yeti", "enabled": True, "volume": 1.0, "mono": True,
                    "auto_detect": {"prefix": False, "keyword": False, "keyword_text": "", "same_app": False,
                                    "anti": False, "anti_keyword_text": ""}}],
        "outputs": [], "noise_suppression": "rnnoise", "volume": 1.0, "muted": False},
    "speaker": {
        "inputs": ["Chromium"],
        "outputs": [{"label": "Ryzen HD Audio Controller Analog Stereo", "enabled": True, "volume": 1.0, "mono": False},
                    {"label": "vencord-screen-share", "enabled": True, "volume": 1.0, "mono": False,
                     "auto_detect": {"prefix": False, "keyword": False, "keyword_text": "", "same_app": True,
                                     "anti": True, "anti_keyword_text": "mic"}}],
        "bypass": [{"label": "spotify", "enabled": True, "volume": 1.5, "mono": False}],
        "bypass_target": "Ryzen HD Audio Controller Analog Stereo", "volume": 1.0, "muted": False},
    "custom": {"next_id": 2, "conduits": [
        {"id": 1, "name": "Game Audio Mix", "as_speaker": True, "as_microphone": True,
         "mic_noise_suppression": "webrtc", "volume": 1.0, "muted": False,
         "inputs": [{"label": "P4next Analog Stereo", "enabled": False, "volume": 1.0, "mono": False}],
         "outputs": []}]},
}
