# The Conduit package, shared by flake.nix (nix run / nix build) and module.nix.
#
#   $out/share/conduit/   conduit_*.py, qt_ui_kit/ (vendored), assets/
#   $out/bin/conduit      the GUI / CLI flags
#   $out/share/icons/...  the logo at every launcher size
#
# Everything runs from the store path, so a rebuild can never leave a stale
# copy behind (the old /etc/conduit/*.py copies are gone; /etc/conduit is now
# just a symlink to share/conduit for poking around).
{ lib, stdenvNoCC, makeWrapper, imagemagick, python3, qt6, pipewire, wireplumber }:

let
  guiPython = python3.withPackages (ps: [ ps.pyside6 ]);
  # PySide6 isn't wrapped by wrapQtAppsHook (it's a library), so the platform
  # plugin path has to be set by hand or every launch fails with "could not
  # find the Qt platform plugin".
  qtPluginPath = "${qt6.qtbase}/lib/qt-6/plugins";
  iconSizes = [ 16 24 32 48 64 128 256 ];
  src = lib.cleanSourceWith {
    src = ./.;
    filter = path: type:
      let base = baseNameOf path; in
      !(lib.hasSuffix ".nix" base || base == "flake.lock" || base == "tests"
        || base == "__pycache__" || lib.hasSuffix ".pyc" base || base == "result");
  };
in
stdenvNoCC.mkDerivation {
  pname = "conduit";
  version = "2.0";
  inherit src;
  nativeBuildInputs = [ makeWrapper imagemagick ];
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    mkdir -p $out/bin $out/share/conduit
    cp -r conduit_*.py qt_ui_kit assets $out/share/conduit/

    makeWrapper ${guiPython}/bin/python3 $out/bin/conduit \
      --add-flags $out/share/conduit/conduit_gui.py \
      --set PYTHONPATH $out/share/conduit \
      --set PYTHONDONTWRITEBYTECODE 1 \
      --set QT_PLUGIN_PATH "${qtPluginPath}" \
      --set QT_QPA_PLATFORM_PLUGIN_PATH "${qtPluginPath}/platforms" \
      --prefix PATH : "${lib.makeBinPath [ pipewire wireplumber ]}"

    ${lib.concatMapStringsSep "\n" (sz: ''
      mkdir -p $out/share/icons/hicolor/${toString sz}x${toString sz}/apps
      magick assets/conduit_logo.png -resize ${toString sz}x${toString sz} \
        $out/share/icons/hicolor/${toString sz}x${toString sz}/apps/conduit.png
    '') iconSizes}
    runHook postInstall
  '';
  meta = {
    description = "PipeWire virtual mic/speaker router with a node-graph editor";
    mainProgram = "conduit";
    platforms = lib.platforms.linux;
  };
}
