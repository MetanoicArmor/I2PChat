{
  description = "I2PChat - Secure peer-to-peer chat over I2P";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        
        python = pkgs.python312;
        textualNoChecks = pkgs.python312Packages.textual.overridePythonAttrs (_old: {
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
        
      in {
        packages.default = pkgs.stdenv.mkDerivation {
          pname = "i2pchat";
          version = "0.2.0";
          
          src = ./.;
          
          nativeBuildInputs = [ pkgs.makeWrapper ];
          buildInputs = [ pythonEnv pkgs.qt6.qtbase ];
          
          dontBuild = true;
          dontWrapQtApps = true;
          
          installPhase = ''
            mkdir -p $out/lib/i2pchat $out/bin
            
            cp -r *.py i2plib $out/lib/i2pchat/
            cp -r icon.png $out/lib/i2pchat/ 2>/dev/null || true
            
            makeWrapper ${pythonEnv}/bin/python $out/bin/i2pchat \
              --add-flags "$out/lib/i2pchat/main_qt.py" \
              --prefix QT_PLUGIN_PATH : "${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}"
            
            makeWrapper ${pythonEnv}/bin/python $out/bin/i2pchat-tui \
              --add-flags "$out/lib/i2pchat/chat-python.py"
          '';
          
          meta = with pkgs.lib; {
            description = "Secure peer-to-peer chat client for the I2P anonymity network";
            homepage = "https://github.com/MetanoicArmor/I2PChat";
            license = licenses.mit;
            platforms = platforms.linux ++ platforms.darwin;
            mainProgram = "i2pchat";
          };
        };
        
        apps.default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/i2pchat";
        };
        
        apps.tui = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/i2pchat-tui";
        };

        checks.default = self.packages.${system}.default;
        
        devShells.default = pkgs.mkShell {
          buildInputs = [
            pythonEnv
            pkgs.qt6.qtbase
          ];
          
          shellHook = ''
            export QT_PLUGIN_PATH="${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}"
            echo "I2PChat development shell"
            echo "Run: python main_qt.py"
          '';
        };
      }
    );
}
