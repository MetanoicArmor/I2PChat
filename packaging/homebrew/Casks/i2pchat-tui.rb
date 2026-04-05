cask "i2pchat-tui" do
  version "1.2.3"
  # Run: ./packaging/refresh-checksums.sh v1.2.3 — use macOS TUI zip line
  sha256 "c9a045aae4bf8e0b6bfe8e0b49c251b59b5fd04067d97666ee279b323c1048e1"

  url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macos-arm64-tui-v#{version}.zip"
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
