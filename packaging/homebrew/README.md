# Homebrew (Cask)

Файл [`Casks/i2pchat.rb`](Casks/i2pchat.rb) — готовый cask для установки `I2PChat.app` из zip релиза GitHub.

## Отдельный tap-репозиторий (рекомендуется)

1. Создайте репозиторий `https://github.com/MetanoicArmor/homebrew-i2pchat`.
2. Скопируйте в корень репозитория каталог `Casks/` с файлом `i2pchat.rb` из этой папки.
3. Пользователи:

```bash
brew tap MetanoicArmor/i2pchat
brew install --cask i2pchat
```

Homebrew по соглашению ожидает репозиторий с именем `homebrew-<tap>`.

## PR в homebrew-cask

Альтернатива — один pull request в [Homebrew/homebrew-cask](https://github.com/Homebrew/homebrew-cask) с тем же содержимым `i2pchat.rb` (после проверки [документации по cask](https://docs.brew.sh/Cask-Cookbook)).

## Обновление версии

После публикации нового тега `vX.Y.Z` на GitHub обновите `version`, `sha256` и при необходимости имя архива в `url`. Быстрее всего: [`../refresh-checksums.sh`](../refresh-checksums.sh) (см. [`../README.md`](../README.md)).
