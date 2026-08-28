# Conduit

<img src="assets/conduit_logo.png" width="120" alt="Conduit logo" />

A PipeWire virtual mic/speaker router for NixOS, with a Qt editor. Two
virtual devices — **Conduit Virtual Speaker** and **Conduit Virtual
Mic** — get created system-wide and set as your defaults. You then
patch real apps and real hardware in and out of them from a small GUI,
and the routing survives reboots.

## What it does

- Creates `Conduit Virtual Speaker` and `Conduit Virtual Mic`, sets
  them as your default output/input.
- Lets you route specific apps into the virtual speaker (for further
  mixing/processing) and specific real mics into the virtual mic.
- Lets you **bypass** an app around the virtual speaker entirely —
  e.g. Spotify goes straight to your real speakers instead of through
  the mix.
- Everything is saved to `~/.config/conduit/state.json` and re-applied
  continuously by a background service, so replugging headphones or
  reopening an app doesn't lose the routing, and nothing needs to be
  redone after a reboot.

## Requirements

Your existing NixOS config needs PipeWire (with the PulseAudio
compatibility layer) already enabled:

```nix
services.pulseaudio.enable = false;
security.rtkit.enable = true;
services.pipewire = {
  enable = true;
  alsa.enable = true;
  pulse.enable = true;
};
```

(This is already the case in your `configuration.nix`.)

## Install

In your system flake:

```nix
inputs = {
  # ...
  conduit = {
    url = "github:mr-tinkle-winkle/conduit";
    inputs.nixpkgs.follows = "nixpkgs";
  };
};

outputs = inputs@{ self, nixpkgs, ... }: {
  nixosConfigurations.mrtw = nixpkgs.lib.nixosSystem {
    # ...
    modules = [
      ./configuration.nix
      inputs.conduit.nixosModules.default
      {
        services.conduit = {
          enable = true;
          user = "mrtw";
        };
      }
    ];
  };
};
```

Then:

```fish
sudo nixos-rebuild switch --flake .
```

Log out and back in (the daemon is a per-user systemd service that
starts with your session), then either run `conduit` from a terminal
or find **Conduit** in your app launcher.

## Using the GUI

Two panels: **Speaker** on the left, **Microphone** on the right.

- **Input** / **Output** dropdowns show whatever's currently eligible
  — for an Input, anything that *produces* audio (a real mic, an
  app that's currently playing sound); for an Output, anything that
  *accepts* audio (a real speaker, an app that's listening). Pick one
  and it's added to the list below; click the ✕ on a row to remove it.
- **Speaker → Bypass**: pick an app here and it's disconnected from
  the virtual speaker entirely, forced instead onto whatever's chosen
  in the **Destination** dropdown right next to it (a single-device
  picker, not a list) -- the two sit side by side since Destination
  only does anything in conjunction with Bypass.
- **Speaker → Output** and **Bypass → Destination** aren't limited
  to physical hardware -- an app that wants to consume your mix (e.g.
  Discord's screen-share audio capture) is fair game for Output too.
  The Speakers picker specifically stays hardware-only, since that's
  "your speakers" in the bypass sense.
- **Every device row has a small `^` button** that opens a popup with
  four combinable Auto-Detect strategies for sweeping in (or keeping
  out) devices you didn't explicitly add. It's per-device, not
  per-list, since a list often mixes real hardware (which never needs
  this) with an app-created virtual device (which might):
  - *Prefix match* -- treats "Chromium input-2" as the same device as
    a saved "Chromium input" (strips a trailing counter/suffix before
    comparing).
  - *Keyword match* -- matches anything whose name contains the text
    you type, e.g. "vencord".
  - *Same source app* -- groups every stream created by the same
    running app/process, however differently each one is named. Only
    kicks in while the device this row was saved as is currently live,
    since that's what supplies the reference to expand from.
  - *Anti-Auto-Detect* -- the inverse: comma-separated keywords that
    keep a matching device OUT of this row's sweep, even if it would
    otherwise match prefix/keyword/same-app. Never removes the row's
    own device, only auto-detected siblings.

  This is built for things like Discord/Vesktop, which spin up a
  differently-named capture stream per screen share. Matches are
  purely a routing-time decision -- they never get written back into
  the visible list, so the GUI keeps showing only what you explicitly
  added even as auto-detected devices come and go.
- **Every device row has an enable checkbox** on the left -- unticking
  it takes that device out of routing entirely (auto-detect included)
  without deleting it, so it's a one-click toggle to bring back later.
- **Every device row has a volume multiplier** (the "1.00x" spinbox) --
  1.00x leaves the device's volume alone, 2.00x doubles it, 0.50x
  halves it. It's continuously re-applied every couple of seconds, so
  it always sits at exactly that level and overrides any manual
  adjustment you make elsewhere (a system volume mixer, the app's own
  volume slider, etc.). Only affects the exact device a row is saved
  as, not any auto-detected siblings it sweeps in. Each row's multiplier
  is fully independent, even for the same physical device added in two
  different lists -- under the hood, a non-1.00x row gets its own tiny
  dedicated volume-adjustment device inserted inline, rather than
  changing the shared device's own volume (which would mean two
  different multipliers on the same device fighting each other).
- **Removing a device actually undoes its connections.** Conduit
  remembers what it last connected across daemon restarts (which
  happen on every save), so taking something out of a list -- or just
  unticking its enable checkbox -- tears down the link it created
  instead of leaving it connected until the next reboot.
- **Auto Refresh Devices**, next to the manual Refresh button,
  periodically re-checks for new/gone devices on its own (every few
  seconds) instead of only refreshing when you click the button.
- **Mono**, on every device row, treats that device as single-channel --
  fanning it out to both stereo channels on the other side (or summing
  a stereo signal down to it), instead of the normal one-to-one channel
  matching that can otherwise leave one side silent for a genuinely
  mono microphone.
- **Noise Suppression**, on the Microphone panel and on any Custom
  Conduit with "As Microphone" ticked, offers RNNoise or WebRTC's
  built-in noise suppression. This one needs an extra step -- see
  below.
- **Close to Tray** hides the window and keeps the daemon-adjacent GUI
  running in the system tray rather than quitting; click the tray icon
  (or its "Open Conduit" menu entry) to bring the window back, or
  "Quit" from the same menu to actually exit. The daemon itself is a
  separate systemd service either way and keeps running regardless of
  whether the GUI window, or the tray icon, is open at all.
- Nothing needs an explicit "Save" — every change writes
  `state.json` immediately and restarts the daemon (debounced by
  ~600ms so rapid changes don't thrash it).
- **Refresh devices** re-queries PipeWire for what's currently
  running — apps only show up in the dropdowns while they're actively
  playing/recording, so if something you want isn't listed yet, open
  it first, then hit Refresh.

### Example: Spotify bypassing the virtual speaker

1. Open Spotify and start playing something (so it shows up as an
   eligible device).
2. In the **Speaker** panel, pick **Analog Stereo Speakers** (or
   whatever your real output is called) in the **Destination** dropdown.
3. Pick **Spotify** in the **Bypass** dropdown.

Spotify now goes straight to your real speakers; everything else you
route through the virtual speaker still gets mixed normally.

## Custom Conduits

Below the Speaker and Microphone panels, **Custom Connection** lets
you build your own virtual patch points — click **+** to add one.
Each Custom Conduit gets:

- **A name** — click it and type; it saves when you click away or hit
  Enter.
- **As Speaker** / **As Microphone** — independently checkable.
  "As Speaker" doesn't need to do much: the conduit is already a
  Sink-style device structurally (same as the main Virtual Speaker),
  so it may appear as a selectable output regardless of this box —
  there's no reliable cross-desktop way to hide a Sink-classed node
  from speaker pickers. "As Microphone" is where it actually does
  something: it creates a second, linked device that taps whatever
  the conduit is carrying and exposes it as a recording device — the
  classic "Stereo Mix" pattern (route game audio into the conduit, get
  it back out both to real speakers *and* as a mic input for Discord).
- **Its own Input and Output lists** — identical in every way to the
  Speaker/Mic panels' lists (enable checkbox, volume multiplier,
  Auto-Detect popup, all of it). Route anything into a conduit, then
  out to anything else, including chaining one custom conduit into
  another.
- **✕ Remove** deletes the whole conduit and tears down its
  connections.

Unlike the two fixed virtual devices, Custom Conduits aren't part of
your NixOS config — they're created and destroyed live by the daemon
at runtime, so there's no rebuild step for adding, renaming, or
removing one. The trade-off: if the daemon isn't running (crashed,
mid-restart), a Custom Conduit's underlying device doesn't exist
either. Renaming takes effect within a couple of seconds -- PipeWire
doesn't support changing a live node's name in place, so under the
hood this is a quick destroy-and-recreate with the new name; any
connections through it get torn down and rebuilt automatically as
part of that, the same brief interruption any other config change
already causes.
## Noise Suppression

Unlike everything else in this app, Noise Suppression needs a one-time
opt-in in your NixOS config before it's usable at all — the actual DSP
(RNNoise, WebRTC) is too complex to trust loading live at runtime the
way Custom Conduits' plain patch-point nodes do, so it's declared
statically instead, the same safe pattern as the two main virtual
devices:

```nix
services.conduit.noiseSuppression.enable = true;
```

Rebuild, and the "Noise Suppression" button on the Microphone panel
(and on any Custom Conduit with "As Microphone" ticked) becomes
functional — picking a method there is instant after that, no further
rebuilds needed.

- **RNNoise** — a dedicated neural-network denoiser (needs nixpkgs'
  `rnnoise-plugin` package, pulled in automatically once the option
  above is enabled). The better-quality option.
- **WebRTC** — PipeWire's built-in noise suppression, no extra package
  needed, but per real-world reports noticeably weaker than RNNoise.
  Included as a no-extra-dependency fallback.

Under the hood, this reserves a small fixed pool of processors (4 of
each method by default — `services.conduit.noiseSuppression.poolSize`
to change it) rather than one per user, since the number of Custom
Conduits is unbounded but the statically-declared processors aren't.
Realistically this only matters if you have more than 4 things
simultaneously wanting the *same* method at once — the Virtual Mic
plus several Custom Conduits, say.

For the Microphone panel specifically, turning this on doesn't just
affect explicit **Output** entries — it also becomes what apps get
when they simply select "Conduit Virtual Mic" as their default input
device, so it doesn't matter which way an app reaches the mic.

## Troubleshooting

Check the daemon's logs:

```fish
journalctl --user -u conduit-daemon -f
```

Each time the daemon (re)starts it logs a one-time inventory of every
node PipeWire currently knows about, including port counts -- if a
device isn't showing up in Conduit's dropdowns, grep for it there:

```fish
journalctl --user -u conduit-daemon -n 300 --no-pager | grep -i vencord
```

`in_ports=0 out_ports=0` on a device means it genuinely has no usable
ports at that moment (e.g. queried before or after the app that owns
it finished setting it up) -- restart the daemon while the device is
actually active and check again.

If `nixos-rebuild` fails on a fresh clone with an error about
`environment.etc."pipewire<...>"` no longer being supported, you're on
an older copy of this repo from before that got fixed -- `git pull`
and rebuild again.

Check the virtual devices exist:

```fish
wpctl status
```

You should see `Conduit Virtual Speaker` and `Conduit Virtual Mic`
listed under Sinks/Sources. If they're missing, `nixos-rebuild switch`
didn't apply, or PipeWire needs a restart to pick up the new
`/etc/pipewire/pipewire.conf.d/` file:

```fish
systemctl --user restart pipewire pipewire-pulse wireplumber
```

Inspect the current graph directly:

```fish
pw-dump | less
```

Restart the daemon manually if needed (the GUI already does this on
every save):

```fish
systemctl --user restart conduit-daemon
```

## Config format

`~/.config/conduit/state.json`, editable by hand if you'd rather skip
the GUI — a `systemctl --user restart conduit-daemon` picks up
changes:

```json
{
  "mic": {
    "inputs": [
      {
        "label": "Blue Yeti Analog Stereo",
        "enabled": true,
        "volume": 1.0,
        "mono": true,
        "auto_detect": {"prefix": false, "keyword": false, "keyword_text": "", "same_app": false, "anti": false, "anti_keyword_text": ""}
      }
    ],
    "outputs": [],
    "noise_suppression": "rnnoise"
  },
  "speaker": {
    "inputs": [],
    "outputs": [
      {"label": "Analog Stereo Speakers", "enabled": true, "volume": 1.0, "mono": false, "auto_detect": {"prefix": false, "keyword": false, "keyword_text": "", "same_app": false, "anti": false, "anti_keyword_text": ""}},
      {"label": "vencord-screen-share", "enabled": true, "volume": 1.0, "mono": false, "auto_detect": {"prefix": false, "keyword": false, "keyword_text": "", "same_app": true, "anti": true, "anti_keyword_text": "mic"}}
    ],
    "bypass": [
      {"label": "Spotify", "enabled": true, "volume": 1.5, "mono": false, "auto_detect": {"prefix": false, "keyword": false, "keyword_text": "", "same_app": false, "anti": false, "anti_keyword_text": ""}}
    ],
    "bypass_target": "Analog Stereo Speakers"
  },
  "custom": {
    "next_id": 2,
    "conduits": [
      {
        "id": 1,
        "name": "Game Audio Mix",
        "as_speaker": true,
        "as_microphone": true,
        "mic_noise_suppression": "webrtc",
        "inputs": [],
        "outputs": []
      }
    ]
  }
}
```

Devices are matched by their `node.description` (stable across boots
for the same hardware); apps are matched by `application.name`. Older
configs from earlier Conduit versions (missing `enabled`/`volume`/
`mono`, a flat list of strings, or a short-lived list-wide auto-detect
format) are migrated automatically on next load -- no manual edits
needed.
There's also a small internal `~/.config/conduit/.link_cache.json` the
daemon uses to track what it last connected across restarts, so it can
undo stale links when you remove or disable something -- not meant to
be hand-edited, safe to delete if it's ever in a confusing state (the
daemon just starts fresh with an empty one).

## Repo layout

```
flake.nix          -- nixosModules.default + a standalone `conduit` package/app
module.nix         -- services.conduit option, virtual device config, systemd service
conduit_daemon.py  -- background router, polls PipeWire every 2s and reconciles links
conduit_gui.py      -- PySide6 editor for state.json
```
