cask "i2pchat" do
  version "1.4.1"

  on_arm do
    sha256 "8f0924b2a744dd506a35cb2c83201bed88ddfe7fee3d1ea1ed227f9cdde039ae"

    url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macOS-arm64-v#{version}.zip"

    # build-macos.sh packs with `ditto --keepParent` → zip root is I2PChat-macOS-*-bundle/
    app "I2PChat-macOS-arm64-bundle/I2PChat.app"
  end
  on_intel do
    sha256 "377da90c95a0a12577f4f1d948f7e25fcf5d70d478d8a5f60a39ef29dd55377a"

    url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macOS-x64-v#{version}.zip"

    app "I2PChat-macOS-x64-bundle/I2PChat.app"
  end

  name "I2PChat"
  desc "Experimental peer-to-peer chat client for the I2P network"
  homepage "https://github.com/MetanoicArmor/I2PChat"

  depends_on macos: ">= :big_sur"

  caveats <<~EOS
    Textual TUI only: install the separate cask `i2pchat-tui`, or use I2PChat.app/Contents/MacOS/I2PChat-tui inside this bundle.
  EOS
end
