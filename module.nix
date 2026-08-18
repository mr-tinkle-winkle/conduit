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

  # The daemon only needs the stdlib (json/subprocess/pathlib) -- it
  # shells out to pw-dump/pw-link/wpctl rather than binding to
  # PipeWire directly, so no extra Python packages are required.
  daemonPython = pkgs.python3;

  # The GUI additionally needs PySide6 to talk to Qt.
  guiPython = pkgs.python3.withPackages (ps: [ ps.pyside6 ]);

  # PySide6 ships with `dontWrapQtApps = true` (it's a library, not an
  # app) -- nothing auto-sets the Qt platform plugin path the way
  # wrapQtAppsHook would for a compiled Qt binary. Since we're wrapping
  # a bare python3 interpreter + script (not something wrapQtAppsHook
  # can detect and patch), these have to be set by hand or every
  # launch fails with "could not find the Qt platform plugin xcb/wayland".
  qtPluginPath = "${pkgs.qt6.qtbase}/lib/qt-6/plugins";

  conduitGui = pkgs.stdenv.mkDerivation {
    pname = "conduit";
    version = "1.0";
    dontUnpack = true;
    nativeBuildInputs = [ pkgs.makeWrapper ];
    installPhase = ''
      mkdir -p $out/bin
      makeWrapper ${guiPython}/bin/python3 $out/bin/conduit \
        --set PYTHONPATH /etc/conduit \
        --add-flags /etc/conduit/conduit_gui.py \
        --set QT_PLUGIN_PATH "${qtPluginPath}" \
        --set QT_QPA_PLATFORM_PLUGIN_PATH "${qtPluginPath}/platforms" \
        --prefix PATH : "${pkgs.pipewire}/bin:${pkgs.wireplumber}/bin"
    '';
  };

  # "audio-card" is a standard freedesktop icon-naming-spec name, so
  # this resolves correctly against whatever icon theme is active
  # (Breeze on your Plasma setup) with no custom art needed.
  conduitDesktopItem = pkgs.makeDesktopItem {
    name = "conduit";
    exec = "conduit";
    icon = "audio-card";
    desktopName = "Conduit";
    comment = "Configure the virtual mic/speaker router";
    categories = [ "AudioVideo" "Audio" ];
  };

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

    # Ship both scripts declaratively, same as Puppetry -- the GUI
    # imports the daemon module at runtime, so both need to land
    # together. `nixos-rebuild switch` is the only thing that updates
    # them; state.json (~/.config/conduit/state.json) is separate and
    # untouched by rebuilds.
    environment.etc = {
      "conduit/conduit_daemon.py".source = ./conduit_daemon.py;
      "conduit/conduit_gui.py".source = ./conduit_gui.py;
    };

    environment.systemPackages = [
      conduitGui
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
    systemd.user.services.conduit-daemon = {
      description = "Conduit virtual mic/speaker router";
      wantedBy = [ "default.target" ];
      path = [ pkgs.pipewire pkgs.wireplumber ];
      serviceConfig = {
        ExecStart = "${daemonPython}/bin/python3 /etc/conduit/conduit_daemon.py";
        Restart = "on-failure";
        RestartSec = 2;
        Environment = "PYTHONUNBUFFERED=1";
      };
    };
  };
}
