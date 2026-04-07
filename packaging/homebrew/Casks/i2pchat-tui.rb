cask "i2pchat-tui" do
  version "1.2.4"
  # Run: ./packaging/refresh-checksums.sh 1.2.4 — use macOS TUI zip line
  sha256 "6b698ff77909242c8b8d3d18213c8ac54ac905d75ce92ba2d08775a0e17215cf"

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
