# Conduit -- NixOS module
#
# Exposes services.conduit.* options. Mirrors the Puppetry module
# pattern:
#
#   imports = [ inputs.conduit.nixosModules.default ];
#   services.conduit = {
#     enable = true;
#     user = "yourusername";
#   };
#
# Then: sudo nixos-rebuild switch, log out and back in, and either run
# `conduit` or find "Conduit" in your app launcher/search menu.
#
# Requires services.pipewire.enable = true (with pulse.enable = true)
# already set elsewhere in your config -- this module only *adds* two
# virtual devices on top of an existing PipeWire setup, it doesn't set
# one up from scratch.

{ config, lib, pkgs, ... }:

let
  cfg = config.services.conduit;

  # The daemon only needs the stdlib (it shells out to pw-dump / pw-link /
  # wpctl / pw-cli), so a bare python3 runs it; the GUI's PySide6 lives in the
  # package's own wrapper (package.nix).
  daemonPython = pkgs.python3;
  conduitPkg = pkgs.callPackage ./package.nix { };

  conduitDesktopItem = pkgs.makeDesktopItem {
    name = "conduit";
    exec = "conduit";
    icon = "conduit";
    desktopName = "Conduit";
    comment = "Route apps, mics and speakers through PipeWire";
    categories = [ "AudioVideo" "Audio" ];
  };

  # Noise suppression processors are created fully on-demand by the
  # daemon (spawned as small dedicated `pipewire -c <config>`
  # subprocesses, one per thing actually using noise suppression --
  # see conduit_daemon.py's NOISE SUPPRESSION section for why this is
  # safer to do dynamically than trying to load a filter-chain module
  # into the running server, and why a static pool was the wrong call).
  # All this option needs to provide statically is the one thing the
  # daemon can't discover on its own: where the RNNoise LADSPA plugin
  # actually lives in the Nix store.
  rnnoiseLadspaPath = "${pkgs.rnnoise-plugin.ladspa}/lib/ladspa/librnnoise_ladspa.so";

in
{
  options.services.conduit = {
    enable = lib.mkEnableOption "the Conduit virtual mic/speaker router and its Qt editor";

    user = lib.mkOption {
      type = lib.types.str;
      example = "max";
      description = ''
        Username the per-user systemd service runs for -- i.e. whose
        PipeWire session Conduit manages. Should be the user who logs
        into the graphical session.
      '';
    };

    noiseSuppression = {
      enable = lib.mkEnableOption ''
        the Noise Suppression option in the Virtual Mic / Custom Conduit
        popups. RNNoise (nixpkgs' rnnoise-plugin) is pulled in
        automatically; WebRTC noise suppression rides on PipeWire's own
        echo-cancel module and needs no extra package, but see the
        README for why it's the weaker of the two. Both are created
        fully on-demand by the daemon -- nothing extra exists in your
        PipeWire graph until you actually pick a method for something,
        and it's torn down again the moment you pick None or remove
        whatever was using it.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [{
      assertion = config.services.pipewire.enable;
      message = "services.conduit requires services.pipewire.enable = true";
    }];

    # Two static virtual devices, declared once here rather than
    # created dynamically by the daemon -- this way they exist the
    # instant PipeWire starts (survives the daemon crashing/restarting)
    # and their node.name is guaranteed stable across boots, which the
    # daemon and GUI both rely on to find them again. Both are built
    # from the same "adapter + support.null-audio-sink" factory, which
    # is the standard PipeWire building block for virtual devices --
    # media.class alone decides whether a given instance behaves as a
    # sink or as a source.
    #
    # This has to go through services.pipewire.extraConfig.pipewire
    # (real Nix attrs, which nixpkgs serializes into a drop-in file
    # under /etc/pipewire/pipewire.conf.d/ itself) rather than writing
    # environment.etc."pipewire/..." directly -- recent nixpkgs added
    # an assertion that blocks the latter.
    #
    # conduit_virtual_speaker: media.class = Audio/Sink. A plain null
    # sink -- apps play into it, and its monitor ports (which pw-dump
    # reports as this same node's output ports) are what the daemon
    # links out to your real speakers.
    #
    # conduit_virtual_mic: media.class = Audio/Source/Virtual. This is
    # the one-node "microphone proxy" pattern (documented on the NixOS
    # and Arch wikis) -- internally it has input ports just like a
    # sink, which is what real mics get linked into, but because its
    # media class is Audio/Source/Virtual it shows up as a normal,
    # selectable recording device in Discord/OBS/etc. No separate
    # loopback pair needed.
    services.pipewire.extraConfig.pipewire."10-conduit-virtual-devices" = {
      "context.objects" = [
        {
          factory = "adapter";
          args = {
            "factory.name" = "support.null-audio-sink";
            "node.name" = "conduit_virtual_speaker";
            "node.description" = "Conduit Virtual Speaker";
            "media.class" = "Audio/Sink";
            "audio.position" = [ "FL" "FR" ];
            "monitor.channel-volumes" = true;
          };
        }
        {
          factory = "adapter";
          args = {
            "factory.name" = "support.null-audio-sink";
            "node.name" = "conduit_virtual_mic";
            "node.description" = "Conduit Virtual Mic";
            "media.class" = "Audio/Source/Virtual";
            "audio.position" = [ "FL" "FR" ];
            "monitor.channel-volumes" = true;
          };
        }
      ];
    };

    # Only declared when explicitly opted into -- pulls in the
    # rnnoise-plugin package the daemon needs at runtime to spawn
    # RNNoise instances on demand. No PipeWire config is declared here
    # at all: unlike the two fixed virtual devices above, noise
    # suppression has nothing statically present -- see
    # conduit_daemon.py's NOISE SUPPRESSION section.

    # Everything runs from the package's store path (so a rebuild can't leave
    # a stale copy behind); /etc/conduit is only a convenience symlink to it.
    # state.json / layout.json in ~/.config/conduit are untouched by rebuilds.
    environment.etc."conduit".source = "${conduitPkg}/share/conduit";

    environment.systemPackages = [
      conduitPkg
      conduitDesktopItem
      pkgs.pipewire   # pw-dump / pw-link on PATH for manual debugging
      pkgs.wireplumber # wpctl likewise
    ];

    # Runs as a per-user service, same lifetime reasoning as Puppetry:
    # starts with the login session and dies with it, since it only
    # needs to exist while someone's actually using audio. wantedBy
    # default.target means it comes up fresh on every login/boot and
    # re-applies state.json from scratch -- that's the "reapply every
    # boot" half of the persistence story; wireplumber's own default
    # sink/source memory covers the rest between the two.
    #
    # KillMode = "control-group" matters specifically for noise
    # suppression: the daemon spawns small dedicated `pipewire -c
    # <config>` helper processes on demand (its own children), and
    # without control-group kill mode, stopping/restarting this service
    # would leave those orphaned and running instead of cleaning them
    # up -- exactly the kind of leak the on-demand design is meant to
    # avoid.
    systemd.user.services.conduit-daemon = {
      description = "Conduit virtual mic/speaker router";
      wantedBy = [ "default.target" ];
      path = [ pkgs.pipewire pkgs.wireplumber ];
      serviceConfig = {
        ExecStart = "${daemonPython}/bin/python3 ${conduitPkg}/share/conduit/conduit_daemon.py";
        Restart = "on-failure";
        RestartSec = 2;
        KillMode = "control-group";
        Environment = [
          "PYTHONUNBUFFERED=1"
          "PYTHONDONTWRITEBYTECODE=1"
        ] ++ lib.optional cfg.noiseSuppression.enable
          "CONDUIT_RNNOISE_LADSPA_PATH=${rnnoiseLadspaPath}";
      };
    };
  };
}
