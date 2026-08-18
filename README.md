# Conduit

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
    "inputs": ["Blue Yeti Analog Stereo"],
    "outputs": []
  },
  "speaker": {
    "inputs": [],
    "outputs": ["Analog Stereo Speakers"],
    "bypass": ["Spotify"],
    "bypass_target": "Analog Stereo Speakers"
  }
}
```

Devices are matched by their `node.description` (stable across boots
for the same hardware); apps are matched by `application.name`.

## Repo layout

```
flake.nix          -- nixosModules.default + a standalone `conduit` package/app
module.nix         -- services.conduit option, virtual device config, systemd service
conduit_daemon.py  -- background router, polls PipeWire every 2s and reconciles links
conduit_gui.py      -- PySide6 editor for state.json
```
