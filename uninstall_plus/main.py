"""Application entry point."""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, VERSION, i18n  # noqa: E402
from .i18n import _  # noqa: E402
from .settings import Settings  # noqa: E402
from .widgets import load_css  # noqa: E402
from .window import MainWindow  # noqa: E402

COLOR_SCHEMES = {
    "system": Adw.ColorScheme.DEFAULT,  # follows the GNOME light/dark preference
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
}


class UninstallPlusApplication(Adw.Application):
    def __init__(self, settings=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.settings = settings or Settings()

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)
        load_css()
        self.apply_theme()
        self._add_action("preferences", self._show_preferences, ["<Control>comma"])
        self._add_action("about", self._show_about)
        self._add_action("quit", lambda: self.quit(), ["<Control>q"])
        self.set_accels_for_action("win.refresh", ["<Control>r", "F5"])
        self.set_accels_for_action("win.search", ["<Control>f"])
        self.set_accels_for_action("win.shortcuts", ["<Control>question"])
        for index in range(1, 4):
            self.set_accels_for_action(f"win.tab-{index}", [f"<Alt>{index}"])

    def do_activate(self):
        window = self.props.active_window or MainWindow(self)
        window.present()

    def _add_action(self, name, callback, accels=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", lambda *_args: callback())
        self.add_action(action)
        if accels:
            self.set_accels_for_action(f"app.{name}", accels)

    # -- Settings ---------------------------------------------------------

    def apply_theme(self):
        Adw.StyleManager.get_default().set_color_scheme(
            COLOR_SCHEMES[self.settings.get("theme")]
        )

    def change_theme(self, theme):
        self.settings.set("theme", theme)
        self.apply_theme()

    def change_language(self, choice):
        """Save the language and rebuild the window so every label is updated."""
        self.settings.set("language", choice)
        if i18n.resolve_language(choice) == i18n.current_language():
            return
        window = self.props.active_window
        if window is not None and window.is_busy():
            # Do not interrupt an uninstall: the choice applies on next start.
            window.toast(_("The new language will be used the next time {app} starts").format(
                app=APP_NAME))
            return
        i18n.set_language(choice)
        # Rebuild after the combo row signal handler has returned.
        GLib.idle_add(self._rebuild_window)

    def _rebuild_window(self):
        old = self.props.active_window
        state = old.get_state() if old else None
        if old is not None and hasattr(old, "get_visible_dialog"):
            dialog = old.get_visible_dialog()
            if dialog is not None:
                dialog.force_close()
        new = MainWindow(self)
        if state:
            new.restore_state(state)
        new.present()
        if old is not None:
            old.destroy()
        # Keep the preferences open so the user sees the result of the change.
        new.show_preferences()
        return GLib.SOURCE_REMOVE

    # -- Dialogs ----------------------------------------------------------

    def _show_preferences(self):
        if self.props.active_window:
            self.props.active_window.show_preferences()

    def _show_about(self):
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            developer_name="3zmana",
            comments=_("See and uninstall the RPM packages and Flatpak applications installed "
                       "on your system, and manage their repositories."),
            license_type=Gtk.License.MIT_X11,
            website="https://github.com/3zmana/uninstall-plus",
            issue_url="https://github.com/3zmana/uninstall-plus/issues",
        )
        # Translators: put your name here; it is shown in the About dialog.
        credits = _("translator-credits")
        if credits != "translator-credits":
            about.set_translator_credits(credits)
        about.present(self.props.active_window)


def main():
    settings = Settings()
    # The language must be chosen before GTK starts so its own strings follow it.
    i18n.set_language(settings.get("language"))
    app = UninstallPlusApplication(settings)
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
