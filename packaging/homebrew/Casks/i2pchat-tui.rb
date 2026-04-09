cask "i2pchat-tui" do
  version "1.2.6"
  # Run: ./packaging/refresh-checksums.sh 1.2.6 — use macOS TUI zip line
  sha256 "50fe60a8ed0d1c6ff2cfcfd6119f74d4e3a7682926f687023309e5e514dae0c1"

  url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macOS-arm64-tui-v#{version}.zip"
  name "I2PChat TUI"
  desc "Terminal UI (Textual) for I2PChat — no PyQt GUI binary"
  homepage "https://github.com/MetanoicArmor/I2PChat"

  depends_on arch: :arm64
  depends_on macos: ">= :big_sur"

  artifact "I2PChat", target: "#{HOMEBREW_PREFIX}/opt/i2pchat-tui/I2PChat"
  binary "i2pchat-tui"

  caveats <<~EOS
    The launcher is copied to your PATH; the PyInstaller bundle is under
    #{HOMEBREW_PREFIX}/opt/i2pchat-tui/I2PChat
    Run: i2pchat-tui [optional profile name]
  EOS
end
