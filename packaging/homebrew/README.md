# Homebrew (Cask)

- [`Casks/i2pchat.rb`](Casks/i2pchat.rb) — GUI: `I2PChat.app` из zip релиза.
- [`Casks/i2pchat-tui.rb`](Casks/i2pchat-tui.rb) — только TUI: zip **`I2PChat-macos-arm64-tui-vX.Y.Z.zip`** (без `.app`).

## Отдельный tap-репозиторий (рекомендуется)

Репозиторий: **[github.com/MetanoicArmor/homebrew-i2pchat](https://github.com/MetanoicArmor/homebrew-i2pchat)** — канонические cask-файлы по-прежнему ведутся в основном репозитории в [`Casks/`](Casks/); при релизе скопируйте обновлённые `.rb` в tap и закоммитьте.

Пользователи — **одной командой** (tap подтянется сам; формат `github-пользователь/короткое-имя-tap/имя-cask`, не `…/tap/…`):

```bash
brew install --cask metanoicarmor/i2pchat/i2pchat
brew install --cask metanoicarmor/i2pchat/i2pchat-tui
```

Классический вариант:

```bash
brew tap MetanoicArmor/i2pchat
brew install --cask i2pchat
brew install --cask i2pchat-tui
```

## PR в homebrew-cask

Альтернатива — один pull request в [Homebrew/homebrew-cask](https://github.com/Homebrew/homebrew-cask) с тем же содержимым `i2pchat.rb` (после проверки [документации по cask](https://docs.brew.sh/Cask-Cookbook)).

## Обновление версии

После публикации нового тега `vX.Y.Z` на GitHub обновите `version`, `sha256` в **обоих** cask. Скрипт [`../refresh-checksums.sh`](../refresh-checksums.sh) печатает строки для GUI и TUI zip.
