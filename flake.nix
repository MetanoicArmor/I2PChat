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

        pythonEnv = python.withPackages (ps:
          (with ps; [
            textualNoChecks
            rich
            pyperclip
            pyqt6
            qasync
            pillow
            pynacl
            keyring
          ])
          ++ lib.optionals pkgs.stdenv.isLinux (with ps; [
            cryptography
          ]));

        qtBasePackages = with pkgs.qt6; [
          qtbase
          qtmultimedia
          qtsvg
          qtimageformats
        ];
        qtPackages = qtBasePackages ++ lib.optionals pkgs.stdenv.isLinux [
          pkgs.qt6.qtwayland
        ];
        # qtPluginPrefix exists on most nixpkgs Qt derivations; fallback for older trees.
        qtPluginPathFor = qtPkg:
          if qtPkg ? qtPluginPrefix then "${qtPkg}/${qtPkg.qtPluginPrefix}"
          else "${qtPkg}/lib/qt-6/plugins";
        qtPluginPath = lib.concatStringsSep ":" (map qtPluginPathFor qtPackages);
        qtQmlPath =
          lib.concatStringsSep ":" (
            lib.filter (path: path != null) (map
              (qtPkg:
                let
                  qmlPath = "${qtPkg}/lib/qt-6/qml";
                in
                if builtins.pathExists qmlPath then qmlPath else null)
              qtPackages)
          );
        runtimeTools = lib.optionals pkgs.stdenv.isLinux (with pkgs; [
          alsa-utils
          libcanberra-gtk3
          libnotify
          pulseaudio
          xdg-utils
        ]);
        runtimeBinPath = lib.makeBinPath runtimeTools;
        xdgDataPath = lib.makeSearchPath "share" [
          pkgs.hicolor-icon-theme
          pkgs.shared-mime-info
        ];
        guiWrapperArgs = lib.concatStringsSep " " (
          [
            ''--prefix PYTHONPATH : "$out/lib/i2pchat"''
            ''--prefix QT_PLUGIN_PATH : "${qtPluginPath}"''
          ]
          ++ lib.optionals (qtQmlPath != "") [
            ''--prefix QML2_IMPORT_PATH : "${qtQmlPath}"''
          ]
          ++ lib.optionals pkgs.stdenv.isLinux [
            ''--prefix PATH : "${runtimeBinPath}"''
            ''--prefix XDG_DATA_DIRS : "$out/share:${xdgDataPath}"''
          ]
        );
        tuiWrapperArgs = lib.concatStringsSep " " (
          [
            ''--prefix PYTHONPATH : "$out/lib/i2pchat"''
          ]
          ++ lib.optionals pkgs.stdenv.isLinux [
            ''--prefix PATH : "${runtimeBinPath}"''
          ]
        );
        devShellLinuxHook = lib.optionalString pkgs.stdenv.isLinux ''
          export XDG_DATA_DIRS="${xdgDataPath}${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"
        '';

        i2pchat = pkgs.stdenv.mkDerivation {
          pname = "i2pchat";
          version = lib.removeSuffix "\n" (lib.removeSuffix "\r" (builtins.readFile ./VERSION));

          src = ./.;

          nativeBuildInputs = [ pkgs.makeWrapper ];
          buildInputs = [ pythonEnv ] ++ qtPackages;

          dontBuild = true;

          installPhase = ''
            runHook preInstall
            mkdir -p "$out/lib/i2pchat" "$out/bin" "$out/share/applications" "$out/share/pixmaps"

            cp -r i2pchat "$out/lib/i2pchat/"
            if [ -e VERSION ]; then cp VERSION "$out/lib/i2pchat/"; fi
            if [ -d assets ]; then cp -r assets "$out/lib/i2pchat/"; fi
            if [ -e icon.png ]; then
              cp icon.png "$out/lib/i2pchat/"
              install -Dm644 icon.png "$out/share/pixmaps/i2pchat.png"
            fi

            makeWrapper ${pythonEnv}/bin/python "$out/bin/i2pchat" \
              --add-flags "-m i2pchat.gui" \
              ${guiWrapperArgs}

            makeWrapper ${pythonEnv}/bin/python "$out/bin/i2pchat-tui" \
              --add-flags "-m i2pchat.tui" \
              ${tuiWrapperArgs}

            cat > "$out/share/applications/i2pchat.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Version=1.0
Name=I2PChat
Comment=Secure peer-to-peer chat client for the I2P anonymity network
Exec=i2pchat
Icon=i2pchat
Categories=Network;InstantMessaging;
Keywords=I2P;chat;messenger;privacy;
Terminal=false
StartupNotify=true
EOF

            cat > "$out/share/applications/i2pchat-tui.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Version=1.0
Name=I2PChat TUI
Comment=Terminal user interface for I2PChat
Exec=i2pchat-tui
Icon=i2pchat
Categories=Network;InstantMessaging;TerminalEmulator;
Keywords=I2P;chat;messenger;privacy;terminal;
Terminal=true
EOF
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
        packages.gui = i2pchat;
        packages.tui = i2pchat;

        apps.default = {
          type = "app";
          program = "${i2pchat}/bin/i2pchat";
        };

        apps.gui = {
          type = "app";
          program = "${i2pchat}/bin/i2pchat";
        };

        apps.tui = {
          type = "app";
          program = "${i2pchat}/bin/i2pchat-tui";
        };

        checks.default = i2pchat;

        devShells.default = pkgs.mkShell {
          buildInputs = [ pythonEnv pkgs.uv ] ++ qtPackages ++ runtimeTools;

          shellHook = ''
            export QT_PLUGIN_PATH="${qtPluginPath}"
            ${lib.optionalString (qtQmlPath != "") ''export QML2_IMPORT_PATH="${qtQmlPath}"''}
            ${devShellLinuxHook}
            echo "I2PChat development shell"
            echo "Run GUI: python -m i2pchat.gui"
            echo "Run TUI: python -m i2pchat.tui"
          '';
        };
      }
    );
}
