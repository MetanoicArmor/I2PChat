cask "i2pchat" do
  version "1.2.6"
  sha256 "efea76282bbca2add5e54adcc2f852c90ea29f77ebc52ba8eb91d102398e8837"

  url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macOS-arm64-v#{version}.zip"
  name "I2PChat"
  desc "Experimental peer-to-peer chat client for the I2P network"
  homepage "https://github.com/MetanoicArmor/I2PChat"

  depends_on arch: :arm64
  depends_on macos: ">= :big_sur"

  # build-macos.sh packs with `ditto --keepParent` → zip root is I2PChat-macOS-arm64-bundle/, not .app
  app "I2PChat-macOS-arm64-bundle/I2PChat.app"

  caveats <<~EOS
    Textual TUI only: install the separate cask `i2pchat-tui`, or use I2PChat.app/Contents/MacOS/I2PChat-tui inside this bundle.
  EOS
end
