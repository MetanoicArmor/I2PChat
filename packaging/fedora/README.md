# Fedora (RPM / COPR)

Пакет [`i2pchat.spec`](i2pchat.spec) собирает **RPM из официального** `I2PChat-linux-x86_64-v<версия>.zip` на GitHub Releases (внутри — AppImage), по той же схеме, что и `.deb` в [`../debian/`](../debian/README.md).

## Почему COPR

[COPR](https://copr.fedorainfracloud.org/) — типичный способ для сторонних бинарных пакетов: пользователи делают `dnf copr enable <user>/i2pchat` и `dnf install i2pchat`, обновления приходят через `dnf`.

Попадание в основной репозиторий Fedora без мейнтейнера и политики сборки из исходников здесь **не цель**.

## Перед сборкой

1. Опубликован тег **`v%{version}`** и на релизе есть **`I2PChat-linux-x86_64-v%{version}.zip`** и ветка/tag **`v%{version}`** с файлом **`icon.png`** (как в upstream).
2. В `i2pchat.spec` обновите директивы **`Version:`** (и при пересборке того же tarball — увеличьте **`Release:`** и допишите запись в **`%changelog`**).

## Локальная проверка (Fedora)

```bash
sudo dnf install rpm-build rpmdevtools
cd packaging/fedora
spectool -g -R i2pchat.spec
rpmbuild -ba ~/rpmbuild/SPECS/i2pchat.spec
# или только SRPM: rpmbuild -bs ~/rpmbuild/SPECS/i2pchat.spec
```

Скопируйте `i2pchat.spec` в `~/rpmbuild/SPECS/` перед `rpmbuild`, если `spectool` кладёт spec не туда — проще:

```bash
mkdir -p ~/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
cp packaging/fedora/i2pchat.spec ~/rpmbuild/SPECS/
spectool -g -R -C ~/rpmbuild/SOURCES ~/rpmbuild/SPECS/i2pchat.spec
rpmbuild -ba ~/rpmbuild/SPECS/i2pchat.spec
```

## Публикация в COPR

1. Зарегистрируйтесь на [copr.fedorainfracloud.org](https://copr.fedorainfracloud.org/) и установите `dnf install copr-cli` (на Fedora).
2. Создайте проект, например `youruser/i2pchat`.
3. Варианты:
   - **Upload SRPM:** соберите SRPM как выше, затем `copr-cli build youruser/i2pchat ~/rpmbuild/SRPMS/i2pchat-*.src.rpm`.
   - **SCM:** укажите Git URL репозитория I2PChat, ветку/тег и путь к spec: `packaging/fedora/i2pchat.spec` (в веб-интерфейсе COPR: *Package source* → git).

После успешной сборки пользователи:

```bash
sudo dnf copr enable youruser/i2pchat
sudo dnf install i2pchat
```

**TUI** в этом пакете не выносится отдельно в `PATH`; он остаётся внутри AppImage (как в документации upstream для Linux).
