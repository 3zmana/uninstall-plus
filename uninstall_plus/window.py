"""Main application window."""

from gi.repository import Adw, Gio, Gtk

from . import APP_NAME, backend
from .flatpak_page import FlatpakPage
from .i18n import N_, _
from .preferences import PreferencesDialog
from .repos_page import ReposPage
from .rpm_page import RpmPage
from .widgets import make_spinner, run_in_thread, show_error

# Keyboard shortcuts; titles are translated when the dialog opens.
SHORTCUTS = [
    ("<Control>f", N_("Search")),
    ("<Control>r", N_("Refresh")),
    ("<Alt>1", N_("RPM Packages")),
    ("<Alt>2", N_("Flatpak")),
    ("<Alt>3", N_("Repositories")),
    ("<Control>comma", N_("Preferences")),
    ("<Control>question", N_("Keyboard Shortcuts")),
    ("<Control>q", N_("Quit")),
]


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1040, 740)
        self.set_size_request(360, 400)
        self._busy = False

        # Header bar with the tab switcher in the middle.
        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack(vexpand=True)
        self.switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(self.switcher)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Refresh (Ctrl+R)"),
                             action_name="win.refresh")
        header.pack_start(refresh)
        self.spinner = make_spinner(16)
        self.spinner.set_visible(False)
        header.pack_start(self.spinner)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("Refresh"), "win.refresh")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Preferences"), "app.preferences")
        section.append(_("Keyboard Shortcuts"), "win.shortcuts")
        section.append(_("About {app}").format(app=APP_NAME), "app.about")
        menu.append_section(None, section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                     tooltip_text=_("Main Menu"))
        if hasattr(menu_button, "set_primary"):
            menu_button.set_primary(True)
        header.pack_end(menu_button)

        # Banner shown while an operation is running.
        self.banner = Adw.Banner()

        # Pages.
        self.rpm_page = RpmPage(self)
        self.flatpak_page = FlatpakPage(self)
        self.repos_page = ReposPage(self)
        self.stack.add_titled_with_icon(self.rpm_page, "rpm", _("RPM Packages"),
                                        "package-x-generic-symbolic")
        self.stack.add_titled_with_icon(self.flatpak_page, "flatpak", _("Flatpak"),
                                        "application-x-executable-symbolic")
        self.stack.add_titled_with_icon(self.repos_page, "repos", _("Repositories"),
                                        "network-server-symbolic")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.banner)
        content.append(self.stack)

        switcher_bar = Adw.ViewSwitcherBar(stack=self.stack)
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.add_bottom_bar(switcher_bar)
        toolbar_view.set_content(content)

        self.toast_overlay = Adw.ToastOverlay(child=toolbar_view)
        self.set_content(self.toast_overlay)

        # On narrow windows, move the tab switcher to the bottom.
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 620sp"))
        breakpoint.add_setter(switcher_bar, "reveal", True)
        breakpoint.add_setter(self.switcher, "visible", False)
        breakpoint.connect("apply", lambda _b: self._set_compact(True))
        breakpoint.connect("unapply", lambda _b: self._set_compact(False))
        self.add_breakpoint(breakpoint)

        self._install_actions()

        for page in (self.rpm_page, self.flatpak_page, self.repos_page):
            page.reload()

    def _install_actions(self):
        refresh = Gio.SimpleAction.new("refresh", None)
        refresh.connect("activate", lambda *_args: self.current_page().reload())
        self.add_action(refresh)

        search = Gio.SimpleAction.new("search", None)
        search.connect("activate", lambda *_args: self.current_page().focus_search())
        self.add_action(search)

        for index, name in enumerate(("rpm", "flatpak", "repos"), start=1):
            action = Gio.SimpleAction.new(f"tab-{index}", None)
            action.connect("activate", lambda *_a, n=name: self.stack.set_visible_child_name(n))
            self.add_action(action)

        shortcuts = Gio.SimpleAction.new("shortcuts", None)
        shortcuts.connect("activate", lambda *_args: self.show_shortcuts())
        self.add_action(shortcuts)

    def show_preferences(self):
        PreferencesDialog(self.get_application()).present(self)

    def show_shortcuts(self):
        """Adw.ShortcutsDialog on libadwaita >= 1.8, Gtk.ShortcutsWindow otherwise."""
        if hasattr(Adw, "ShortcutsDialog"):
            dialog = Adw.ShortcutsDialog()
            section = Adw.ShortcutsSection.new(None)
            for accel, title in SHORTCUTS:
                section.add(Adw.ShortcutsItem.new(_(title), accel))
            dialog.add(section)
            dialog.present(self)
            return
        group = Gtk.ShortcutsGroup(title=_("General"))
        for accel, title in SHORTCUTS:
            group.add_shortcut(Gtk.ShortcutsShortcut(accelerator=accel, title=_(title)))
        section = Gtk.ShortcutsSection(section_name="main")
        section.add_group(group)
        window = Gtk.ShortcutsWindow(modal=True, transient_for=self)
        window.add_section(section)
        window.present()

    def _set_compact(self, compact):
        for page in (self.rpm_page, self.flatpak_page, self.repos_page):
            page.set_compact(compact)

    def current_page(self):
        return self.stack.get_visible_child()

    # -- State kept when the window is rebuilt (language change) -----------

    def get_state(self):
        width, height = self.get_default_size()
        return {
            "size": (self.get_width() or width, self.get_height() or height),
            "maximized": self.is_maximized(),
            "page": self.stack.get_visible_child_name(),
        }

    def restore_state(self, state):
        self.set_default_size(*state["size"])
        if state["maximized"]:
            self.maximize()
        self.stack.set_visible_child_name(state["page"])

    # -- Feedback ---------------------------------------------------------

    def toast(self, text):
        self.toast_overlay.add_toast(Adw.Toast(title=text, timeout=3))

    def is_busy(self):
        return self._busy

    def _set_busy(self, busy, text=""):
        self._busy = busy
        self.spinner.set_visible(busy)
        self.banner.set_title(text)
        self.banner.set_revealed(busy)

    def run_task(self, func, args, on_success, error_heading, busy_text=None,
                 on_error=None):
        """Run a system-changing operation in the background.

        Only one such operation runs at a time. Errors are shown in a dialog.
        """
        if self._busy:
            self.toast(_("Please wait for the current operation to finish"))
            if on_error:
                on_error()
            return
        self._set_busy(True, busy_text or _("Working…"))

        def done(result, error):
            self._set_busy(False)
            if error:
                if on_error:
                    on_error()
                if not isinstance(error, backend.AuthenticationCancelled):
                    show_error(self, error_heading, error)
                return
            on_success(result)

        run_in_thread(func, done, *args)
