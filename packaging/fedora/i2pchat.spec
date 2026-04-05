# RPM spec for COPR: repackage upstream Linux AppImage from GitHub Releases.
# Bump Version (and Release if rebuilding the same version) on each upstream tag.

Name:           i2pchat
Version:        1.2.2
Release:        1%{?dist}
Summary:        Experimental peer-to-peer chat client for I2P (upstream AppImage)

License:        AGPL-3.0-or-later
URL:            https://github.com/MetanoicArmor/I2PChat
ExclusiveArch:  x86_64

Source0:        https://github.com/MetanoicArmor/I2PChat/releases/download/v%{version}/I2PChat-linux-x86_64-v%{version}.zip
Source1:        https://github.com/MetanoicArmor/I2PChat/raw/v%{version}/icon.png

BuildRequires:  unzip

Requires:       hicolor-icon-theme
Requires:       zlib

%description
I2PChat is a cross-platform chat client for the I2P network (PyQt6 GUI and optional
Textual TUI). This package installs the upstream PyInstaller-built AppImage from
GitHub Releases under /opt/i2pchat and adds /usr/bin/i2pchat plus a .desktop entry.

%prep
cd "%{_builddir}"
rm -rf "%{name}-%{version}"
mkdir "%{name}-%{version}"
cd "%{name}-%{version}"
cp "%{SOURCE1}" ./icon.png
unzip -q "%{SOURCE0}"

%build
# Binary bundle from upstream; nothing to compile.

%install
cd "%{_builddir}/%{name}-%{version}"
install -d "%{buildroot}/opt/i2pchat"
install -p -m 0755 "I2PChat-linux-x86_64-v%{version}.AppImage" \
  "%{buildroot}/opt/i2pchat/I2PChat.AppImage"
install -d "%{buildroot}%{_bindir}"
ln -sf /opt/i2pchat/I2PChat.AppImage "%{buildroot}%{_bindir}/i2pchat"
install -d "%{buildroot}%{_datadir}/pixmaps"
install -p -m 0644 icon.png "%{buildroot}%{_datadir}/pixmaps/i2pchat.png"
install -d "%{buildroot}%{_datadir}/applications"
cat > "%{buildroot}%{_datadir}/applications/i2pchat.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=I2P Chat
Comment=Secure chat over I2P
Exec=/usr/bin/i2pchat %u
Icon=i2pchat
Terminal=false
Categories=Network;Chat;
EOF

%files
%dir /opt/i2pchat
/opt/i2pchat/I2PChat.AppImage
%{_bindir}/i2pchat
%{_datadir}/applications/i2pchat.desktop
%{_datadir}/pixmaps/i2pchat.png

%changelog
* Sun Apr 05 2026 MetanoicArmor <https://github.com/MetanoicArmor/I2PChat> - 1.2.2-1
- Package upstream AppImage (x86_64 zip from GitHub Releases)
