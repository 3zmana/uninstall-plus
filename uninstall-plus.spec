Name:           uninstall-plus
Version:        1.0.1
Release:        2%{?dist}
Summary:        See and uninstall RPM packages and Flatpak applications

License:        MIT
URL:            https://github.com/3zmana/uninstall-plus
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  make
BuildRequires:  gettext
BuildRequires:  python3
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib

Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita >= 1.5
Requires:       rpm
Requires:       dnf5
# "dnf copr" and "dnf config-manager" are provided by the dnf5 plugins.
Requires:       dnf5-plugins
Requires:       flatpak
Requires:       polkit
Requires:       hicolor-icon-theme

%description
Uninstall Plus is a GNOME application for Fedora that lists every installed
RPM package with its description and origin repository, including software
installed from the command line, and every Flatpak application. Each one can
be uninstalled with a single click after a clear confirmation.

It also manages DNF repositories (from a .repo URL, COPR projects or manual
entries) and Flatpak remotes: add, remove, enable and disable.

%prep
%autosetup

%build
# Pure Python application: only the translations are compiled.
%make_build locale

%install
%make_install PREFIX=%{_prefix} LIBEXEC=%{_libexecdir}
%find_lang %{name}

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github._3zmana.UninstallPlus.desktop
appstream-util validate-relax --nonet %{buildroot}%{_metainfodir}/io.github._3zmana.UninstallPlus.metainfo.xml
python3 -m unittest discover -s tests

%files -f %{name}.lang
%license LICENSE
%doc README.md CHANGELOG.md
%{_bindir}/uninstall-plus
%{_libexecdir}/uninstall-plus/
%{_datadir}/uninstall-plus/
%{_datadir}/applications/io.github._3zmana.UninstallPlus.desktop
%{_metainfodir}/io.github._3zmana.UninstallPlus.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/io.github._3zmana.UninstallPlus.svg
%{_datadir}/icons/hicolor/symbolic/apps/io.github._3zmana.UninstallPlus-symbolic.svg
%{_datadir}/polkit-1/actions/io.github._3zmana.UninstallPlus.policy

%changelog
* Wed Oct 21 2026 3zmana <19848572+3zmana@users.noreply.github.com> - 1.0.1-2
- Application ID is now io.github._3zmana.UninstallPlus
- License changed to MIT
- First public release on GitHub

* Wed Oct 21 2026 3zmana <19848572+3zmana@users.noreply.github.com> - 1.0.1-1
- Add a Preferences dialog to the main menu
- Interface language: System (default), English or French; French translation
- Color theme: System (default), Light or Dark

* Wed Oct 21 2026 3zmana <19848572+3zmana@users.noreply.github.com> - 1.0.0-2
- Keep the search bar and filters in the same centered column as the lists

* Wed Oct 21 2026 3zmana <19848572+3zmana@users.noreply.github.com> - 1.0.0-1
- First release
