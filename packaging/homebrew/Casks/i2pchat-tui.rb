cask "i2pchat-tui" do
  version "1.3.1"

  on_arm do
    sha256 "e8ff104a152b30323626cbce7ae3b4a2362f7abae4677a85872c81d3f0bb42ae"

    url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macOS-arm64-tui-v#{version}.zip"
  end
  on_intel do
    sha256 "48ef4c35860570176a28ed3c0b8eb488a4ef6368a6c5c513a9e4918c0d2b8f57"

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
