"""Details dialog shared by the RPM and Flatpak tabs."""

from gi.repository import Adw, Gtk

from .i18n import _
from .widgets import dim, make_spinner


class DetailsDialog(Adw.Dialog):
    """Shows information about one package and offers to uninstall it."""

    def __init__(self, title, summary, icon_name=None, on_uninstall=None):
        super().__init__(title=title, content_width=540, content_height=640)
        self._on_uninstall = on_uninstall

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.page = Adw.PreferencesPage()
        toolbar.set_content(self.page)
        self.set_child(toolbar)

        # Header: icon, name and summary.
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         halign=Gtk.Align.CENTER, margin_bottom=6)
        icon = Gtk.Image(icon_name=icon_name or "package-x-generic-symbolic", pixel_size=80)
        if (icon_name or "").endswith("-symbolic"):
            dim(icon)
        else:
            icon.add_css_class("icon-dropshadow")
        name = Gtk.Label(label=title, wrap=True, justify=Gtk.Justification.CENTER)
        name.add_css_class("title-1")
        header.append(icon)
        header.append(name)
        if summary:
            sub = dim(Gtk.Label(label=summary, wrap=True, justify=Gtk.Justification.CENTER))
            header.append(sub)
        if on_uninstall:
            button = Gtk.Button(label=_("Uninstall"), halign=Gtk.Align.CENTER, margin_top=12)
            button.add_css_class("pill")
            button.add_css_class("destructive-action")
            button.connect("clicked", self._uninstall_clicked)
            header.append(button)
        header_group = Adw.PreferencesGroup()
        header_group.add(header)
        self.page.add(header_group)

        self.spinner_group = Adw.PreferencesGroup()
        spinner = make_spinner(32)
        spinner.set_halign(Gtk.Align.CENTER)
        self.spinner_group.add(spinner)
        self.spinner_group.set_visible(False)
        self.page.add(self.spinner_group)

    def _uninstall_clicked(self, _button):
        self.close()
        self._on_uninstall()

    def set_loading(self, loading):
        self.spinner_group.set_visible(loading)

    def add_group(self, title, rows):
        """Add a group of (label, value) rows. Empty values are skipped."""
        group = Adw.PreferencesGroup(title=title)
        added = False
        for label, value in rows:
            if not value:
                continue
            row = Adw.ActionRow(title=label, subtitle=value, use_markup=False,
                                subtitle_selectable=True)
            row.add_css_class("property")
            group.add(row)
            added = True
        if added:
            self.page.add(group)
        return group

    def add_text_group(self, title, text):
        group = Adw.PreferencesGroup(title=title)
        label = Gtk.Label(label=text, wrap=True, xalign=0, selectable=True,
                          margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        frame = Gtk.Box()
        frame.add_css_class("card")
        frame.append(label)
        group.add(frame)
        self.page.add(group)
        return group
