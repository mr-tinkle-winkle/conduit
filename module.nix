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

  # Icon lookup by name (as used in the .desktop entry below) walks
  # share/icons/hicolor/<size>/apps/<name>.<ext> under each
  # XDG_DATA_DIRS entry, so it has to be installed at exactly this
  # path layout to be found by launchers/taskbars at all. Resized at
  # build time with ImageMagick from the single source logo rather
  # than committing a pile of pre-scaled duplicates to the repo --
  # same approach as Puppetry.
  conduitIconSizes = [ 16 24 32 48 64 128 256 ];

  conduitGui = pkgs.stdenv.mkDerivation {
    pname = "conduit";
    version = "1.0";
    dontUnpack = true;
    nativeBuildInputs = [ pkgs.makeWrapper pkgs.imagemagick ];
    installPhase = ''
      mkdir -p $out/bin
      makeWrapper ${guiPython}/bin/python3 $out/bin/conduit \
        --set PYTHONPATH /etc/conduit \
        --add-flags /etc/conduit/conduit_gui.py \
        --set QT_PLUGIN_PATH "${qtPluginPath}" \
        --set QT_QPA_PLATFORM_PLUGIN_PATH "${qtPluginPath}/platforms" \
        --prefix PATH : "${pkgs.pipewire}/bin:${pkgs.wireplumber}/bin"

      ${lib.concatMapStringsSep "\n" (sz: ''
        mkdir -p $out/share/icons/hicolor/${toString sz}x${toString sz}/apps
        convert ${./assets/conduit_logo.png} -resize ${toString sz}x${toString sz} \
          $out/share/icons/hicolor/${toString sz}x${toString sz}/apps/conduit.png
      '') conduitIconSizes}
    '';
  };

  conduitDesktopItem = pkgs.makeDesktopItem {
    name = "conduit";
    exec = "conduit";
    icon = "conduit";
    desktopName = "Conduit";
    comment = "Configure the virtual mic/speaker router";
    categories = [ "AudioVideo" "Audio" ];
  };

  # A fixed-size pool of noise-suppression processors per method, rather
  # than one dynamically created per consumer -- this keeps the DSP part
  # (the genuinely complex bit, filter-chain plugin graphs) in the same
  # statically-declared, once-verified pattern as the two main virtual
  # devices, instead of attempting to load filter-chain modules live at
  # runtime the way Custom Conduits create their plain adapter nodes.
  # The daemon allocates a free slot to whatever currently wants it and
  # links through it -- see conduit_daemon.py's NOISE SUPPRESSION
  # section. 4 of each is enough for realistic simultaneous use (the
  # Virtual Mic plus a few Custom Conduits); raise noiseSuppressionPoolSize
  # if you genuinely need more.
  noiseSuppressionPoolSize = cfg.noiseSuppression.poolSize;

  # RNNoise (via the LADSPA plugin in nixpkgs' rnnoise-plugin package) --
  # the higher-quality of the two options, a dedicated neural-network
  # denoiser with no other side effects.
  rnnoiseProcessors = lib.genList (i:
    let n = toString (i + 1); in {
      name = "libpipewire-module-filter-chain";
      args = {
        "node.description" = "Conduit Noise Suppression (RNNoise) ${n}";
        "filter.graph" = {
          nodes = [{
            type = "ladspa";
            name = "rnnoise";
            plugin = "${pkgs.rnnoise-plugin.ladspa}/lib/ladspa/librnnoise_ladspa.so";
            label = "noise_suppressor_stereo";
            control = { "VAD Threshold (%)" = 50.0; };
          }];
        };
        "capture.props" = {
          "node.name" = "conduit_ns_rnnoise_${n}_in";
          "media.class" = "Audio/Sink";
          "audio.position" = [ "FL" "FR" ];
        };
        "playback.props" = {
          "node.name" = "conduit_ns_rnnoise_${n}_out";
          "media.class" = "Audio/Source";
          "audio.position" = [ "FL" "FR" ];
        };
      };
    }
  ) noiseSuppressionPoolSize;

  # WebRTC's built-in noise suppression, riding on PipeWire's own
  # echo-cancel module rather than a separate plugin -- no extra package
  # needed, but per real-world reports it's noticeably weaker than
  # RNNoise (see README). Used here in "standalone" mode: the
  # sink/playback side that would normally carry the echo-reference
  # signal is left completely unconnected, so nothing is actually
  # echo-cancelled -- only the webrtc.noise_suppression effect applies.
  webrtcProcessors = lib.genList (i:
    let n = toString (i + 1); in {
      name = "libpipewire-module-echo-cancel";
      args = {
        "library.name" = "aec/libspa-aec-webrtc";
        "aec.args" = "webrtc.noise_suppression=true webrtc.gain_control=true webrtc.high_pass_filter=true webrtc.voice_detection=false";
        "capture.props" = { "node.name" = "conduit_ns_webrtc_${n}_in"; "node.passive" = true; };
        "source.props" = { "node.name" = "conduit_ns_webrtc_${n}_out"; };
        "sink.props" = { "node.name" = "conduit_ns_webrtc_${n}_sink_unused"; "node.passive" = true; };
        "playback.props" = { "node.name" = "conduit_ns_webrtc_${n}_playback_unused"; "node.passive" = true; };
      };
    }
  ) noiseSuppressionPoolSize;

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
        popups, backed by a fixed pool of statically-declared RNNoise and
        WebRTC-noise-suppression processors
      '';

      poolSize = lib.mkOption {
        type = lib.types.ints.positive;
        default = 4;
        description = ''
          How many simultaneous users of EACH method (RNNoise, WebRTC)
          to provision for. Only the Virtual Mic and Custom Conduits with
          "As Microphone" ticked can request noise suppression, so this
          rarely needs to be more than a handful.
        '';
      };
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
    # rnnoise-plugin package and creates 2*poolSize extra PipeWire nodes
    # that most people won't want by default.
    services.pipewire.extraConfig.pipewire."11-conduit-noise-suppression" =
      lib.mkIf cfg.noiseSuppression.enable {
        "context.modules" = rnnoiseProcessors ++ webrtcProcessors;
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
