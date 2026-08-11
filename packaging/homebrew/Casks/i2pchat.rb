cask "i2pchat" do
  version "1.4.0"

  on_arm do
    sha256 "ac8530b491fd0a07d13a48313fa9bb4a4e4a10577959b3eb6b92dbef1912faa8"

    url "https://github.com/MetanoicArmor/I2PChat/releases/download/v#{version}/I2PChat-macOS-arm64-v#{version}.zip"

    # build-macos.sh packs with `ditto --keepParent` → zip root is I2PChat-macOS-*-bundle/
    app "I2PChat-macOS-arm64-bundle/I2PChat.app"
  end
  on_intel do
    sha256 "1550a96ceb112f3e40bb3de907f730a31ba4577f4ad81aae759911cbb6516987"

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
