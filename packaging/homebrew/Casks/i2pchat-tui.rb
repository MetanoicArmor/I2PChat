cask "i2pchat-tui" do
  version "1.4.1"

  on_arm do
    sha256 "d9617476c41a64ecaeb0a610449b927c4d2be54d5c75e6afca97e04fdb646204"

    url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macOS-arm64-tui-v#{version}.zip"
  end
  on_intel do
    sha256 "160a7e06e19729d0ed294c1f590123fd098183fd07a7922dcf53a5f94cd8988f"

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
