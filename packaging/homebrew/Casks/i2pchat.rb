cask "i2pchat" do
  version "1.2.1"
  sha256 "57830938cae8e776956590d87c286446b322e1945805424cfbbd75439e783ead"

  url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macOS-arm64-v#{version}.zip"
  name "I2PChat"
  desc "Experimental peer-to-peer chat client for the I2P network"
  homepage "https://github.com/MetanoicArmor/I2PChat"

  depends_on arch: :arm64
  depends_on macos: ">= :big_sur"

  app "I2PChat.app"

  caveats <<~EOS
    Console TUI (optional): I2PChat.app/Contents/MacOS/I2PChat-tui
  EOS
end
