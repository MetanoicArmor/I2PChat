{
  description = "I2PChat - Secure peer-to-peer chat over I2P";

  inputs = {
    # Reproducibility via flake.lock; update with: nix flake update
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        lib = pkgs.lib;

        python = pkgs.python312;
        textualNoChecks = python.pkgs.textual.overridePythonAttrs (_old: {
          doCheck = false;
        });

        pythonEnv = python.withPackages (ps: with ps; [
          textualNoChecks
          rich
          pyperclip
          pyqt6
          qasync
          pillow
          pynacl
        ]);

        qtbase = pkgs.qt6.qtbase;
        # qtPluginPrefix exists on most nixpkgs qt6.qtbase; fallback for older trees
        qtPluginPath =
          if qtbase ? qtPluginPrefix then "${qtbase}/${qtbase.qtPluginPrefix}"
          else "${qtbase}/lib/qt-6/plugins";

        i2pchat = pkgs.stdenv.mkDerivation {
          pname = "i2pchat";
          version = lib.removeSuffix "\n" (lib.removeSuffix "\r" (builtins.readFile ./VERSION));

          src = ./.;

          nativeBuildInputs = [ pkgs.makeWrapper ];
          buildInputs = [ pythonEnv qtbase ];

          dontBuild = true;

          installPhase = ''
            runHook preInstall
            mkdir -p "$out/lib/i2pchat" "$out/bin"

            cp -r i2pchat "$out/lib/i2pchat/"
            mkdir -p "$out/lib/i2pchat/vendor"
            cp -r vendor/i2plib "$out/lib/i2pchat/vendor/"
            if [ -e VERSION ]; then cp VERSION "$out/lib/i2pchat/"; fi
            if [ -d assets ]; then cp -r assets "$out/lib/i2pchat/"; fi
            if [ -e icon.png ]; then cp icon.png "$out/lib/i2pchat/"; fi

            makeWrapper ${pythonEnv}/bin/python "$out/bin/i2pchat" \
              --add-flags "-m i2pchat.gui" \
              --prefix PYTHONPATH : "$out/lib/i2pchat" \
              --prefix QT_PLUGIN_PATH : "${qtPluginPath}"

            makeWrapper ${pythonEnv}/bin/python "$out/bin/i2pchat-tui" \
              --add-flags "-m i2pchat.gui.chat_python" \
              --prefix PYTHONPATH : "$out/lib/i2pchat"
            runHook postInstall
          '';

          meta = with lib; {
            description = "Secure peer-to-peer chat client for the I2P anonymity network";
            homepage = "https://github.com/MetanoicArmor/I2PChat";
            license = licenses.agpl3Plus;
            platforms = platforms.linux ++ platforms.darwin;
            mainProgram = "i2pchat";
          };
        };
      in
      {
        packages.default = i2pchat;

        apps.default = {
          type = "app";
          program = "${i2pchat}/bin/i2pchat";
        };

        apps.tui = {
          type = "app";
          program = "${i2pchat}/bin/i2pchat-tui";
        };

        checks.default = i2pchat;

        devShells.default = pkgs.mkShell {
          buildInputs = [
            pythonEnv
            qtbase
          ];

          shellHook = ''
            export QT_PLUGIN_PATH="${qtPluginPath}"
            echo "I2PChat development shell"
            echo "Run: python -m i2pchat.gui.main_qt"
          '';
        };
      }
    );
}
