"""The "Flatpak" tab."""

from gi.repository import Adw, GLib, Gtk

from . import backend
from .details import DetailsDialog
from .i18n import _, localize_size_text, ngettext
from .widgets import (CONTENT_THRESHOLD, CONTENT_WIDTH, centered, dim, loading_page,
                      run_in_thread, status_page, tag)


def app_icon_name(widget, app_id):
    """Use the app's exported icon when the icon theme knows it."""
    theme = Gtk.IconTheme.get_for_display(widget.get_display())
    return app_id if theme.has_icon(app_id) else "application-x-executable-symbolic"


def installation_label(installation):
    """Translated name of a Flatpak installation."""
    if installation == "system":
        return _("System")
    if installation == "user":
        return _("User")
    return installation


class FlatpakRow(Adw.ActionRow):
    def __init__(self, page, app):
        super().__init__(title=app.name, subtitle=app.description or app.app_id,
                         use_markup=False, activatable=True)
        self.app = app
        self.search_text = f"{app.name}\n{app.app_id}\n{app.description}".lower()
        self.set_subtitle_lines(1)
        self.set_title_lines(1)
        self.set_tooltip_text(f"{app.app_id} {app.version}".strip())

        icon = Gtk.Image(icon_name=app_icon_name(page, app.app_id), pixel_size=40)
        icon.add_css_class("icon-dropshadow")
        self.add_prefix(icon)

        self.origin_tag = tag(app.origin or _("unknown"), tooltip=_("Remote"))
        self.add_suffix(self.origin_tag)
        self.add_suffix(tag(installation_label(app.installation), app.installation,
                            tooltip=_("Installation")))
        self.size_label = dim(Gtk.Label(label=localize_size_text(app.size),
                                        valign=Gtk.Align.CENTER, width_chars=8, xalign=1))
        self.size_label.add_css_class("caption")
        self.size_label.add_css_class("numeric")
        self.add_suffix(self.size_label)

        button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=_("Uninstall"))
        button.add_css_class("flat")
        button.connect("clicked", lambda _b: page.uninstall(app))
        self.add_suffix(button)
        self.connect("activated", lambda _r: page.show_details(app))
        self.set_compact(page.compact)

    def set_compact(self, compact):
        """On narrow windows, keep only the essentials."""
        self.origin_tag.set_visible(not compact)
        self.size_label.set_visible(not compact)


class FlatpakPage(Gtk.Box):
    """List and uninstall Flatpak applications."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.loading = False
        self.compact = False
        self.rows = []

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bar.add_css_class("filter-bar")
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Search applications"),
                                            width_chars=14, hexpand=True)
        self.search_entry.connect("search-changed", lambda _e: self._refilter())
        bar.append(self.search_entry)
        self.counter = dim(Gtk.Label(valign=Gtk.Align.CENTER))
        self.counter.add_css_class("counter")
        bar.append(self.counter)
        self.append(centered(bar))
        self.append(Gtk.Separator())

        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                    valign=Gtk.Align.START)
        self.list_box.add_css_class("boxed-list")
        self.list_box.set_filter_func(self._filter_func)
        clamp = Adw.Clamp(maximum_size=CONTENT_WIDTH, tightening_threshold=CONTENT_THRESHOLD,
                          child=self.list_box,
                          margin_top=12, margin_bottom=24, margin_start=12, margin_end=12)
        scrolled = Gtk.ScrolledWindow(child=clamp, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(loading_page(_("Reading Flatpak applications…")), "loading")
        self.stack.add_named(scrolled, "list")
        self.stack.add_named(status_page("system-search-symbolic", _("No Applications Found"),
                                         _("Try a different search.")), "no-results")
        self.stack.add_named(status_page("application-x-executable-symbolic",
                                         _("No Flatpak Applications"),
                                         _("Applications installed with Flatpak will appear here.")),
                             "empty")
        self.error_page = status_page("dialog-error-symbolic",
                                      _("Could Not Read Flatpak Applications"))
        self.stack.add_named(self.error_page, "error")
        self.append(self.stack)

    # -- Filtering --------------------------------------------------------

    def _filter_func(self, row):
        query = self.search_entry.get_text().strip().lower()
        return not query or all(word in row.search_text for word in query.split())

    def _refilter(self):
        self.list_box.invalidate_filter()
        if self.loading or self.stack.get_visible_child_name() in ("error", "empty", "loading"):
            return
        shown = sum(1 for row in self.rows if self._filter_func(row))
        total = len(self.rows)
        if shown != total:
            text = ngettext("{shown} of {total} app", "{shown} of {total} apps", total)
        else:
            text = ngettext("{total} app", "{total} apps", total)
        self.counter.set_text(text.format(shown=shown, total=total))
        self.stack.set_visible_child_name("list" if shown else "no-results")

    def focus_search(self):
        self.search_entry.grab_focus()

    def set_compact(self, compact):
        self.compact = compact
        for row in self.rows:
            row.set_compact(compact)

    # -- Loading ----------------------------------------------------------

    def reload(self):
        if self.loading:
            return
        self.loading = True
        if not self.rows:
            self.stack.set_visible_child_name("loading")
        run_in_thread(backend.list_flatpak_apps, self._on_loaded)

    def _on_loaded(self, apps, error):
        self.loading = False
        for row in self.rows:
            self.list_box.remove(row)
        self.rows = []
        if error:
            self.error_page.set_description(GLib.markup_escape_text(str(error)))
            self.stack.set_visible_child_name("error")
            self.counter.set_text("")
            return
        if not apps:
            self.stack.set_visible_child_name("empty")
            self.counter.set_text("")
            return
        for app in apps:
            row = FlatpakRow(self, app)
            self.rows.append(row)
            self.list_box.append(row)
        self.stack.set_visible_child_name("list")
        self._refilter()

    # -- Actions ----------------------------------------------------------

    def show_details(self, app):
        dialog = DetailsDialog(
            title=app.name,
            summary=app.description,
            icon_name=app_icon_name(self, app.app_id),
            on_uninstall=lambda: self.uninstall(app),
        )
        dialog.add_group(_("Information"), [
            (_("Application ID"), app.app_id),
            (_("Version"), app.version),
            (_("Branch"), app.branch),
            (_("Remote"), app.origin),
            (_("Installation"), installation_label(app.installation)),
            (_("Installed Size"), localize_size_text(app.size)),
            (_("Reference"), app.ref),
        ])
        dialog.present(self.window)

    def uninstall(self, app):
        if app.installation == "system":
            body = _("{name} will be removed for all users. Runtimes that are no longer used "
                     "by any application will be removed as well.")
        else:
            body = _("{name} will be removed for your user account. Runtimes that are no "
                     "longer used by any application will be removed as well.")
        dialog = Adw.AlertDialog(heading=_("Uninstall {name}?").format(name=app.name),
                                 body=body.format(name=app.name))
        delete_data = Gtk.CheckButton(label=_("Also delete the app's data and settings"))
        dialog.set_extra_child(delete_data)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("uninstall", _("Uninstall"))
        dialog.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response != "uninstall":
                return
            self.window.run_task(
                backend.uninstall_flatpak, (app, delete_data.get_active()),
                on_success=lambda _out: self._on_removed(app),
                error_heading=_("Could Not Uninstall “{name}”").format(name=app.name),
                busy_text=_("Uninstalling {name}…").format(name=app.name),
            )

        dialog.connect("response", on_response)
        dialog.present(self.window)

    def _on_removed(self, app):
        self.window.toast(_("{name} was uninstalled").format(name=app.name))
        self.reload()
