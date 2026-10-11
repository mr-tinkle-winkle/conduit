{
  description = "Conduit -- PipeWire virtual mic/speaker router + node-graph editor for NixOS";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      # In your own flake:
      #   imports = [ inputs.conduit.nixosModules.default ];
      #   services.conduit = { enable = true; user = "yourusername"; };
      nixosModules.default = import ./module.nix;

      # `nix run github:mr-tinkle-winkle/conduit` opens the GUI for a quick look.
      # Without the module there are no virtual devices and no daemon, so
      # nothing actually routes.
      packages.${system} = {
        default = self.packages.${system}.conduit;
        conduit = pkgs.callPackage ./package.nix { };
      };

      apps.${system}.default = {
        type = "app";
        program = "${self.packages.${system}.conduit}/bin/conduit";
      };
    };
}
