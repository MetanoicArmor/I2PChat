cask "i2pchat" do
  version "1.2.3"
  sha256 "14b1f40685a21df49ef9bb62ed82798f4300006f58d7590ee09dbd6a75bb53d0"

  url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macOS-arm64-v#{version}.zip"
  name "I2PChat"
  desc "Experimental peer-to-peer chat client for the I2P network"
  homepage "https://github.com/MetanoicArmor/I2PChat"

  depends_on arch: :arm64
  depends_on macos: ">= :big_sur"

  app "I2PChat.app"

  caveats <<~EOS
    Textual TUI only: install the separate cask `i2pchat-tui`, or use I2PChat.app/Contents/MacOS/I2PChat-tui inside this bundle.
  EOS
end
