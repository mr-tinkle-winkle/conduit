{
  description = "Conduit -- PipeWire virtual mic/speaker router + Qt editor for NixOS";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      # Add this to your own flake's inputs, then:
      #   imports = [ inputs.conduit.nixosModules.default ];
      #   services.conduit = { enable = true; user = "yourusername"; };
      nixosModules.default = import ./module.nix;

      # Lets you build/run the GUI directly without the full module,
      # e.g. `nix run github:mr-tinkle-winkle/conduit` -- handy for a
      # quick look. Note this standalone path won't have the virtual
      # devices or the daemon service the module sets up, so there's
      # nothing to actually route until the module is installed too.
      packages.${system} = {
        default = self.packages.${system}.conduit;

        conduit = pkgs.stdenv.mkDerivation {
          pname = "conduit";
          version = "1.0";
          dontUnpack = true;
          nativeBuildInputs = [ pkgs.makeWrapper ];
          installPhase =
            let
              guiPython = pkgs.python3.withPackages (ps: [ ps.pyside6 ]);
              qtPluginPath = "${pkgs.qt6.qtbase}/lib/qt-6/plugins";
            in
            ''
              mkdir -p $out/bin $out/share/conduit
              cp ${./conduit_daemon.py} $out/share/conduit/conduit_daemon.py
              cp ${./conduit_gui.py} $out/share/conduit/conduit_gui.py
              makeWrapper ${guiPython}/bin/python3 $out/bin/conduit \
                --set PYTHONPATH $out/share/conduit \
                --add-flags $out/share/conduit/conduit_gui.py \
                --set QT_PLUGIN_PATH "${qtPluginPath}" \
                --set QT_QPA_PLATFORM_PLUGIN_PATH "${qtPluginPath}/platforms" \
                --prefix PATH : "${pkgs.pipewire}/bin:${pkgs.wireplumber}/bin"
            '';
        };
      };

      apps.${system}.default = {
        type = "app";
        program = "${self.packages.${system}.conduit}/bin/conduit";
      };
    };
}
