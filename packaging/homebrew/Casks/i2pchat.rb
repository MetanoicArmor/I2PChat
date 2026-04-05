cask "i2pchat" do
  version "1.2.2"
  sha256 "1cad7f66fe7e7cf5b743108d09d4358a30177977bc5c752bcb8a8e4922c2dc48"

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
