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
  in the **Speakers** dropdown right below it (a single-device picker,
  not a list).
- **Speaker → Output** and **Bypass target (Speakers)** aren't limited
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
  as, not any auto-detected siblings it sweeps in.
- **Removing a device actually undoes its connections.** Conduit
  remembers what it last connected across daemon restarts (which
  happen on every save), so taking something out of a list -- or just
  unticking its enable checkbox -- tears down the link it created
  instead of leaving it connected until the next reboot.
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
   whatever your real output is called) in the **Speakers** dropdown.
3. Pick **Spotify** in the **Bypass** dropdown.

Spotify now goes straight to your real speakers; everything else you
route through the virtual speaker still gets mixed normally.

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
        "auto_detect": {"prefix": false, "keyword": false, "keyword_text": "", "same_app": false, "anti": false, "anti_keyword_text": ""}
      }
    ],
    "outputs": []
  },
  "speaker": {
    "inputs": [],
    "outputs": [
      {"label": "Analog Stereo Speakers", "enabled": true, "volume": 1.0, "auto_detect": {"prefix": false, "keyword": false, "keyword_text": "", "same_app": false, "anti": false, "anti_keyword_text": ""}},
      {"label": "vencord-screen-share", "enabled": true, "volume": 1.0, "auto_detect": {"prefix": false, "keyword": false, "keyword_text": "", "same_app": true, "anti": true, "anti_keyword_text": "mic"}}
    ],
    "bypass": [
      {"label": "Spotify", "enabled": true, "volume": 1.5, "auto_detect": {"prefix": false, "keyword": false, "keyword_text": "", "same_app": false, "anti": false, "anti_keyword_text": ""}}
    ],
    "bypass_target": "Analog Stereo Speakers"
  }
}
```

Devices are matched by their `node.description` (stable across boots
for the same hardware); apps are matched by `application.name`. Older
configs from earlier Conduit versions (missing `enabled`/`volume`, a
flat list of strings, or a short-lived list-wide auto-detect format)
are migrated automatically on next load -- no manual edits needed.
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
