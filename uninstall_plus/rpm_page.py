"""The "RPM Packages" tab."""

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from . import backend
from .details import DetailsDialog
from .i18n import _, format_size, ngettext
from .widgets import (CONTENT_THRESHOLD, CONTENT_WIDTH, centered, dim, ellipsizing_dropdown,
                      loading_page, make_wrap_box, output_view, run_in_thread, status_page, tag)


class PackageItem(GObject.Object):
    """GObject wrapper so packages can live in a Gio.ListStore."""

    __gtype_name__ = "UninstallPlusPackageItem"

    def __init__(self, pkg):
        super().__init__()
        self.pkg = pkg
        self.repo_label = backend.pretty_repo_name(pkg.repo)
        # Pre-computed lowercase text makes searching 2000+ rows instant.
        self.search_text = f"{pkg.name}\n{pkg.summary}".lower()


class PackageRow(Gtk.Box):
    """One line of the package list."""

    def __init__(self, on_uninstall, compact=False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.add_css_class("package-row")
        self.item = None
        self.compact = compact

        icon = dim(Gtk.Image(icon_name="package-x-generic-symbolic", pixel_size=24))
        self.append(icon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                       valign=Gtk.Align.CENTER)
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.name_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.name_label.add_css_class("package-name")
        self.version_label = dim(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
        self.version_label.add_css_class("caption")
        title.append(self.name_label)
        title.append(self.version_label)
        self.summary_label = dim(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
        text.append(title)
        text.append(self.summary_label)
        self.append(text)

        self.repo_tag = tag("")
        self.append(self.repo_tag)

        self.size_label = dim(Gtk.Label(xalign=1, width_chars=9))
        self.size_label.add_css_class("caption")
        self.size_label.add_css_class("numeric")
        self.append(self.size_label)

        button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=_("Uninstall"))
        button.add_css_class("flat")
        button.connect("clicked", lambda _b: self.item and on_uninstall(self.item.pkg))
        self.append(button)
        self.set_compact(compact)

    def set_compact(self, compact):
        """On narrow windows, hide the size and show the repository inline."""
        self.compact = compact
        self.repo_tag.set_visible(not compact)
        self.size_label.set_visible(not compact)
        if self.item is not None:
            self._update_version_line()

    def _update_version_line(self):
        pkg = self.item.pkg
        text = f"{pkg.version} · {pkg.arch}"
        if self.compact:
            text = f"{self.item.repo_label} · {text}"
        self.version_label.set_text(text)

    def bind(self, item):
        self.item = item
        pkg = item.pkg
        self.name_label.set_text(pkg.name)
        self._update_version_line()
        self.summary_label.set_text(pkg.summary or _("No description"))
        self.repo_tag.set_text(item.repo_label)
        self.repo_tag.set_tooltip_text(
            _("Repository: {repo}").format(repo=pkg.repo or _("unknown")))
        classes = ["tag"]
        if pkg.repo.startswith("copr:"):
            classes.append("copr")
        self.repo_tag.set_css_classes(classes)
        self.size_label.set_text(format_size(pkg.size) if pkg.size else "")


class RpmPage(Gtk.Box):
    """List, filter and uninstall RPM packages."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.loading = False
        # Entries of the repository filter; index 0 is "All Repositories".
        self.repo_choices = [_("All Repositories")]
        self.compact = False
        self.rows = []  # live row widgets, to switch them to compact mode

        self.append(centered(self._build_filter_bar()))
        self.append(Gtk.Separator())

        self.store = Gio.ListStore(item_type=PackageItem)
        self.filter = Gtk.CustomFilter.new(self._filter_func)
        self.filter_model = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.filter_model.connect("items-changed", lambda *_args: self._update_counter())

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        factory.connect("teardown", self._on_teardown)
        self.list_view = Gtk.ListView(
            model=Gtk.NoSelection(model=self.filter_model),
            factory=factory,
            single_click_activate=True,
            show_separators=True,
        )
        self.list_view.add_css_class("rich-list")
        self.list_view.connect("activate", self._on_activate)
        clamp = Adw.ClampScrollable(maximum_size=CONTENT_WIDTH,
                                    tightening_threshold=CONTENT_THRESHOLD,
                                    child=self.list_view)
        scrolled = Gtk.ScrolledWindow(child=clamp, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(loading_page(_("Reading installed packages…")), "loading")
        self.stack.add_named(scrolled, "list")
        self.stack.add_named(
            status_page("system-search-symbolic", _("No Packages Found"),
                        _("Try a different search or change the filters.")),
            "empty",
        )
        self.error_page = status_page("dialog-error-symbolic", _("Could Not Read Packages"))
        retry = Gtk.Button(label=_("Try Again"), halign=Gtk.Align.CENTER)
        retry.add_css_class("pill")
        retry.connect("clicked", lambda _b: self.reload())
        self.error_page.set_child(retry)
        self.stack.add_named(self.error_page, "error")
        self.append(self.stack)

    # -- Building ---------------------------------------------------------

    def _build_filter_bar(self):
        # Search on its own full-width line, filters below (they wrap when narrow).
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.add_css_class("filter-bar")
        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_("Search packages by name or description"),
            width_chars=14, hexpand=True)
        self.search_entry.connect("search-changed", lambda _e: self._refilter())
        outer.append(self.search_entry)
        bar = make_wrap_box()
        outer.append(bar)

        self.repo_dropdown = ellipsizing_dropdown(self.repo_choices)
        self.repo_dropdown.set_tooltip_text(_("Filter by repository"))
        self.repo_dropdown.set_enable_search(True)
        self.repo_dropdown.set_expression(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        )
        self.repo_dropdown.connect("notify::selected", lambda *_args: self._refilter())
        bar.append(self.repo_dropdown)

        user_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                           valign=Gtk.Align.CENTER)
        user_label = Gtk.Label(label=_("Installed by me only"))
        self.user_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.user_switch.set_tooltip_text(
            _("Hide packages that were installed automatically as dependencies")
        )
        self.user_switch.connect("notify::active", lambda *_args: self._refilter())
        user_label.set_mnemonic_widget(self.user_switch)
        user_box.append(user_label)
        user_box.append(self.user_switch)
        bar.append(user_box)

        self.counter = dim(Gtk.Label(valign=Gtk.Align.CENTER))
        self.counter.add_css_class("counter")
        bar.append(self.counter)
        return outer

    def _on_setup(self, _factory, list_item):
        row = PackageRow(self.uninstall, self.compact)
        self.rows.append(row)
        list_item.set_child(row)

    def _on_teardown(self, _factory, list_item):
        row = list_item.get_child()
        if row in self.rows:
            self.rows.remove(row)

    def set_compact(self, compact):
        self.compact = compact
        for row in self.rows:
            row.set_compact(compact)

    def _on_bind(self, _factory, list_item):
        list_item.get_child().bind(list_item.get_item())

    # -- Filtering --------------------------------------------------------

    def _filter_func(self, item):
        if self.user_switch.get_active() and not item.pkg.user_installed:
            return False
        selected = self.repo_dropdown.get_selected()
        if 0 < selected < len(self.repo_choices) and item.repo_label != self.repo_choices[selected]:
            return False
        query = self.search_entry.get_text().strip().lower()
        return not query or all(word in item.search_text for word in query.split())

    def _refilter(self):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_counter()

    def _update_counter(self):
        shown, total = self.filter_model.get_n_items(), self.store.get_n_items()
        if self.loading:
            self.counter.set_text("")
            return
        if shown != total:
            text = ngettext("{shown} of {total} package", "{shown} of {total} packages", total)
        else:
            text = ngettext("{total} package", "{total} packages", total)
        self.counter.set_text(text.format(shown=shown, total=total))
        if self.stack.get_visible_child_name() in ("list", "empty"):
            self.stack.set_visible_child_name("list" if shown else "empty")

    def focus_search(self):
        self.search_entry.grab_focus()

    # -- Loading ----------------------------------------------------------

    def reload(self):
        if self.loading:
            return
        self.loading = True
        if self.store.get_n_items() == 0:
            self.stack.set_visible_child_name("loading")
        run_in_thread(backend.list_rpm_packages, self._on_loaded)

    def _on_loaded(self, result, error):
        self.loading = False
        if error:
            self.error_page.set_description(GLib.markup_escape_text(str(error)))
            self.stack.set_visible_child_name("error")
            self.counter.set_text("")
            return

        # Repository choices for the dropdown, keeping the current selection.
        selected = self.repo_dropdown.get_selected()
        previous = self.repo_choices[selected] if 0 < selected < len(self.repo_choices) else None
        labels = sorted({backend.pretty_repo_name(p.repo) for p in result.packages}, key=str.lower)
        self.repo_choices = [_("All Repositories")] + labels
        self.repo_dropdown.set_model(Gtk.StringList.new(self.repo_choices))
        self.repo_dropdown.set_selected(
            self.repo_choices.index(previous) if previous in labels else 0
        )
        self.repo_dropdown.set_sensitive(result.repo_info_available)

        self.user_switch.set_sensitive(result.user_info_available)
        if not result.user_info_available:
            self.user_switch.set_active(False)
            self.user_switch.set_tooltip_text(_("DNF could not tell which packages you installed"))

        items = [PackageItem(p) for p in result.packages]
        self.store.splice(0, self.store.get_n_items(), items)
        self.stack.set_visible_child_name("list")
        self._refilter()
        if result.warning:
            self.window.toast(_("Some package information is unavailable"))

    # -- Actions ----------------------------------------------------------

    def _on_activate(self, _list_view, position):
        item = self.filter_model.get_item(position)
        if item is not None:
            self.show_details(item)

    def show_details(self, item):
        pkg = item.pkg
        dialog = DetailsDialog(
            title=pkg.name,
            summary=pkg.summary,
            icon_name="package-x-generic-symbolic",
            on_uninstall=lambda: self.uninstall(pkg),
        )
        installed_on = ""
        if pkg.install_time:
            installed_on = GLib.DateTime.new_from_unix_local(pkg.install_time).format("%x %X")
        if pkg.repo in ("", item.repo_label):
            repository = item.repo_label
        else:
            repository = f"{item.repo_label} ({pkg.repo})"
        dialog.add_group(_("Information"), [
            (_("Version"), pkg.version),
            (_("Architecture"), pkg.arch),
            (_("Repository"), repository),
            (_("Installed Size"), format_size(pkg.size) if pkg.size else _("Unknown")),
            (_("Installed On"), installed_on or _("Unknown")),
            (_("Install Reason"),
             _("Installed by you") if pkg.user_installed else _("Dependency or system")),
        ])
        dialog.set_loading(True)
        dialog.present(self.window)

        def on_details(details, error):
            dialog.set_loading(False)
            if error:
                dialog.add_text_group(_("Description"),
                                      _("Could not read details: {error}").format(error=error))
                return
            dialog.add_group(_("Details"), [
                (_("License"), details.license),
                (_("Website"), details.url),
                (_("Vendor"), details.vendor),
                (_("Source Package"), details.source_rpm),
            ])
            dialog.add_text_group(_("Description"), details.description or pkg.summary)

        run_in_thread(backend.get_rpm_details, on_details, pkg.nevra)

    def uninstall(self, pkg):
        """Show what DNF would remove, then uninstall after confirmation."""
        self.window.run_task(
            backend.preview_rpm_removal, (pkg.nevra,),
            on_success=lambda preview: self._confirm_uninstall(pkg, preview),
            error_heading=_("Cannot Uninstall “{name}”").format(name=pkg.name),
            busy_text=_("Checking what will be removed…"),
        )

    def _confirm_uninstall(self, pkg, preview):
        count = backend.removal_count(preview)
        if count > 1:
            body = ngettext(
                "{count} package will be removed, including the packages that depend on "
                "{name} and the dependencies that are no longer needed.",
                "{count} packages will be removed, including the packages that depend on "
                "{name} and the dependencies that are no longer needed.",
                count,
            ).format(count=count, name=pkg.name)
        else:
            body = _("{name} {version} will be removed from your system.").format(
                name=pkg.name, version=pkg.version)
        dialog = Adw.AlertDialog(heading=_("Uninstall {name}?").format(name=pkg.name), body=body)
        if hasattr(dialog, "set_prefer_wide_layout"):
            dialog.set_prefer_wide_layout(True)
        expander = Gtk.Expander(label=_("Transaction Details"), expanded=count > 1)
        expander.set_child(output_view(backend.clean_transaction_text(preview)))
        dialog.set_extra_child(expander)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("uninstall", _("Uninstall"))
        dialog.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response != "uninstall":
                return
            self.window.run_task(
                backend.remove_rpm, (pkg.nevra,),
                on_success=lambda _out: self._on_removed(pkg),
                error_heading=_("Could Not Uninstall “{name}”").format(name=pkg.name),
                busy_text=_("Uninstalling {name}…").format(name=pkg.name),
            )

        dialog.connect("response", on_response)
        dialog.present(self.window)

    def _on_removed(self, pkg):
        self.window.toast(_("{name} was uninstalled").format(name=pkg.name))
        self.reload()
