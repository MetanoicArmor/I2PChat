cask "i2pchat-tui" do
  version "1.4.0"

  on_arm do
    sha256 "66380c09ec1d85120eb62fa9219bf5016da444b02117d8c7fb90d4f9de160abd"

    url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macOS-arm64-tui-v#{version}.zip"
  end
  on_intel do
    sha256 "ffae5d20bec1403b81cdf6c13391654d7cc72b5c4c2660e47bad37cdb222e4ed"

    url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macOS-x64-tui-v#{version}.zip"
  end

  name "I2PChat TUI"
  desc "Terminal UI (Textual) for I2PChat — no PyQt GUI binary"
  homepage "https://github.com/MetanoicArmor/I2PChat"

  depends_on macos: ">= :big_sur"

  binary "i2pchat-tui"
  artifact "I2PChat", target: "#{HOMEBREW_PREFIX}/opt/i2pchat-tui/I2PChat"

  caveats <<~EOS
    The launcher is copied to your PATH; the PyInstaller bundle is under
    #{HOMEBREW_PREFIX}/opt/i2pchat-tui/I2PChat
    Run: i2pchat-tui [optional profile name]
  EOS
end
