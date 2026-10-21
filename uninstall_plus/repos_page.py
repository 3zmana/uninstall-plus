"""The "Repositories" tab: DNF repositories and Flatpak remotes."""

from gi.repository import Adw, Gio, GLib, Gtk

from . import backend
from .flatpak_page import installation_label
from .i18n import _
from .widgets import entry_list, loading_page, run_in_thread, status_page, tag

FLATHUB_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"


def _switch(active, on_toggle):
    """A switch that calls on_toggle(switch, new_state) when the user flips it.

    on_toggle must call revert() when the change failed.
    """
    switch = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
    switch._reverting = False

    def notify(sw, _pspec):
        if sw._reverting:
            return
        on_toggle(sw, sw.get_active())

    switch.connect("notify::active", notify)
    return switch


def revert(switch):
    switch._reverting = True
    switch.set_active(not switch.get_active())
    switch._reverting = False


def _trash_button(tooltip, sensitive, on_click):
    button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                        tooltip_text=tooltip, sensitive=sensitive)
    button.add_css_class("flat")
    button.connect("clicked", lambda _b: on_click())
    return button


class ReposPage(Gtk.Box):
    """Manage DNF repositories and Flatpak remotes."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.loading = False
        self.dnf_rows = []
        self.remote_rows = []
        self.dnf_repos = []

        self._install_actions()

        self.page = Adw.PreferencesPage()

        # Display options.
        options = Adw.PreferencesGroup()
        self.debug_row = Adw.SwitchRow(
            title=_("Show Debug and Source Repositories"),
            subtitle=_("These repositories are only useful to developers"),
        )
        self.debug_row.connect("notify::active", lambda *_args: self._apply_debug_filter())
        options.add(self.debug_row)
        self.page.add(options)

        # DNF repositories.
        self.dnf_group = Adw.PreferencesGroup(
            title=_("DNF Repositories"),
            description=_("Sources used to install and update RPM packages."),
        )
        menu = Gio.Menu()
        menu.append(_("From a .repo File URL…"), "repos.add-url")
        menu.append(_("COPR Project…"), "repos.add-copr")
        menu.append(_("Manual Entry…"), "repos.add-manual")
        add_dnf = Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=menu,
                                 valign=Gtk.Align.CENTER, tooltip_text=_("Add Repository"))
        add_dnf.add_css_class("flat")
        self.dnf_group.set_header_suffix(add_dnf)
        self.page.add(self.dnf_group)

        # Flatpak remotes.
        self.remote_group = Adw.PreferencesGroup(
            title=_("Flatpak Remotes"),
            description=_("Sources used to install and update Flatpak applications."),
        )
        add_remote = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text=_("Add Flatpak Remote"),
                                action_name="repos.add-remote")
        add_remote.add_css_class("flat")
        self.remote_group.set_header_suffix(add_remote)
        self.page.add(self.remote_group)

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(loading_page(_("Reading repositories…")), "loading")
        self.stack.add_named(self.page, "page")
        self.error_page = status_page("dialog-error-symbolic", _("Could Not Read Repositories"))
        self.stack.add_named(self.error_page, "error")
        self.append(self.stack)

    def _install_actions(self):
        group = Gio.SimpleActionGroup()
        for name, callback in [
            ("add-url", self._add_from_url),
            ("add-copr", self._add_copr),
            ("add-manual", self._add_manual),
            ("add-remote", self._add_remote),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            group.add_action(action)
        self.insert_action_group("repos", group)

    def focus_search(self):
        """This tab has no search field."""

    def set_compact(self, compact):
        """The preference rows already adapt to narrow windows."""

    # -- Loading ----------------------------------------------------------

    def reload(self):
        if self.loading:
            return
        self.loading = True
        if not self.dnf_rows and not self.remote_rows:
            self.stack.set_visible_child_name("loading")
        run_in_thread(self._load_all, self._on_loaded)

    @staticmethod
    def _load_all():
        """Read both kinds of repositories; failures are reported per section."""
        result = {}
        for key, func in (("dnf", backend.list_dnf_repos), ("flatpak", backend.list_flatpak_remotes)):
            try:
                result[key] = (func(), None)
            except Exception as exc:  # shown inside the matching group
                result[key] = ([], exc)
        return result

    def _on_loaded(self, result, error):
        self.loading = False
        if error:
            self.error_page.set_description(GLib.markup_escape_text(str(error)))
            self.stack.set_visible_child_name("error")
            return
        self._fill_dnf(*result["dnf"])
        self._fill_remotes(*result["flatpak"])
        self.stack.set_visible_child_name("page")

    def _clear(self, group, rows):
        for row in rows:
            group.remove(row)
        rows.clear()

    def _error_row(self, error):
        row = Adw.ActionRow(title=str(error), subtitle=getattr(error, "details", ""),
                            use_markup=False)
        row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        return row

    # -- DNF repositories -------------------------------------------------

    def _fill_dnf(self, repos, error):
        self._clear(self.dnf_group, self.dnf_rows)
        self.dnf_repos = repos
        if error:
            row = self._error_row(error)
            self.dnf_rows.append(row)
            self.dnf_group.add(row)
            return
        rows = [self._dnf_row(repo) for repo in repos]
        if not rows:
            rows = [Adw.ActionRow(title=_("No DNF repositories found"),
                                  subtitle=_("Repository files are read from /etc/yum.repos.d"))]
        for row in rows:
            self.dnf_rows.append(row)
            self.dnf_group.add(row)
        self._apply_debug_filter()

    def _dnf_row(self, repo):
        subtitle = repo.repo_id
        if repo.owner_package:
            subtitle = _("{id} · from {package}").format(id=repo.repo_id,
                                                         package=repo.owner_package)
        row = Adw.ActionRow(title=repo.name, subtitle=subtitle, use_markup=False)
        row.set_subtitle_lines(1)
        row.repo = repo
        tooltip = [_("File: {path}").format(path=repo.file_path)]
        if repo.url:
            tooltip.append(_("URL: {url}").format(url=repo.url))
        if repo.owner_package:
            tooltip.append(_("Provided by: {package}").format(package=repo.owner_package))
        row.set_tooltip_text("\n".join(tooltip))

        if repo.copr:
            row.add_suffix(tag("COPR", "copr", tooltip=repo.copr))

        row.add_suffix(_switch(repo.enabled, lambda sw, on, r=repo: self._toggle_dnf(sw, r, on)))

        if repo.removable:
            tooltip_text = _("Remove Repository")
        elif repo.owner_package:
            tooltip_text = _("Provided by the package “{package}”. Disable it instead.").format(
                package=repo.owner_package)
        else:
            tooltip_text = _("System repository. Disable it instead.")
        row.add_suffix(_trash_button(tooltip_text, repo.removable,
                                     lambda r=repo: self._remove_dnf(r)))
        return row

    def _apply_debug_filter(self):
        show_debug = self.debug_row.get_active()
        for row in self.dnf_rows:
            repo = getattr(row, "repo", None)
            if repo is not None:
                row.set_visible(show_debug or not repo.is_debug_or_source)

    def _toggle_dnf(self, switch, repo, enabled):
        if enabled:
            done, heading, busy = (_("{name} enabled"), _("Could Not Enable Repository"),
                                   _("Enabling {name}…"))
        else:
            done, heading, busy = (_("{name} disabled"), _("Could Not Disable Repository"),
                                   _("Disabling {name}…"))
        self.window.run_task(
            backend.set_dnf_repo_enabled, (repo.repo_id, enabled),
            on_success=lambda _o: self.window.toast(done.format(name=repo.name)),
            error_heading=heading,
            busy_text=busy.format(name=repo.name),
            on_error=lambda: revert(switch),
        )

    def _remove_dnf(self, repo):
        siblings = [r.repo_id for r in self.dnf_repos
                    if r.file_path == repo.file_path and r.repo_id != repo.repo_id]
        body = _("“{name}” will be removed. Packages already installed from it stay "
                 "installed, but they will no longer receive updates.").format(name=repo.name)
        if repo.copr:
            body += "\n\n" + _("The COPR project {project} will be removed with all its "
                               "repositories.").format(project=repo.copr)
        elif siblings:
            body += "\n\n" + _("Other repositories in {path} are kept.").format(
                path=repo.file_path)
        self._confirm(
            _("Remove Repository?"), body, _("Remove"),
            lambda: self.window.run_task(
                backend.remove_dnf_repo, (repo.repo_id,),
                on_success=lambda _o: self._changed(
                    _("{name} removed").format(name=repo.name)),
                error_heading=_("Could Not Remove Repository"),
                busy_text=_("Removing {name}…").format(name=repo.name),
            ),
        )

    # -- Adding DNF repositories -------------------------------------------

    def _input_dialog(self, heading, body, rows, action_label, is_valid, on_accept):
        """Generic dialog with entry rows; the action is enabled when valid."""
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.set_extra_child(entry_list(*rows))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", action_label)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def update(*_args):
            dialog.set_response_enabled("ok", is_valid())

        for row in rows:
            if isinstance(row, Adw.EntryRow):
                row.connect("changed", update)
                # Pressing Enter triggers the default ("ok") response when it is enabled.
                row.set_activates_default(True)
        update()
        dialog.connect("response", lambda _d, response: response == "ok" and on_accept())
        dialog.present(self.window)
        if rows:
            rows[0].grab_focus()

    def _add_from_url(self):
        url_row = Adw.EntryRow(title=_("Repository File URL"), input_purpose=Gtk.InputPurpose.URL)
        self._input_dialog(
            _("Add Repository From URL"),
            _("Enter the address of a .repo file, for example one provided by a software vendor."),
            [url_row], _("Add"),
            lambda: backend.is_valid_url(url_row.get_text()),
            lambda: self.window.run_task(
                backend.add_dnf_repo_from_url, (url_row.get_text().strip(),),
                on_success=lambda _o: self._changed(_("Repository added")),
                error_heading=_("Could Not Add Repository"),
                busy_text=_("Adding repository…"),
            ),
        )

    def _add_copr(self):
        project_row = Adw.EntryRow(title=_("Project (owner/project)"))
        self._input_dialog(
            _("Enable COPR Project"),
            _("COPR repositories are built by the community and are not reviewed by Fedora. "
              "Only enable projects you trust."),
            [project_row], _("Enable"),
            lambda: bool(backend.COPR_RE.match(project_row.get_text().strip())),
            lambda: self.window.run_task(
                backend.enable_copr, (project_row.get_text().strip(),),
                on_success=lambda _o: self._changed(_("COPR project enabled")),
                error_heading=_("Could Not Enable COPR Project"),
                busy_text=_("Enabling COPR project…"),
            ),
        )

    def _add_manual(self):
        id_row = Adw.EntryRow(title=_("Identifier (e.g. my-repo)"))
        name_row = Adw.EntryRow(title=_("Name"))
        url_row = Adw.EntryRow(title=_("Base URL"), input_purpose=Gtk.InputPurpose.URL)
        gpgcheck_row = Adw.SwitchRow(title=_("Check Package Signatures"), active=True)
        key_row = Adw.EntryRow(title=_("GPG Key URL (optional)"),
                               input_purpose=Gtk.InputPurpose.URL)
        enabled_row = Adw.SwitchRow(title=_("Enabled"), active=True)
        existing = {r.repo_id for r in self.dnf_repos}

        def is_valid():
            repo_id = id_row.get_text().strip()
            key = key_row.get_text().strip()
            return (bool(backend.REPO_ID_RE.match(repo_id)) and repo_id not in existing
                    and bool(name_row.get_text().strip())
                    and "\n" not in name_row.get_text()
                    and backend.is_valid_url(url_row.get_text())
                    and (not key or backend.is_valid_url(key)))

        def accept():
            self.window.run_task(
                backend.add_dnf_repo_manual,
                (id_row.get_text().strip(), name_row.get_text().strip(),
                 url_row.get_text().strip(), gpgcheck_row.get_active(),
                 key_row.get_text().strip(), enabled_row.get_active()),
                on_success=lambda _o: self._changed(_("Repository added")),
                error_heading=_("Could Not Add Repository"),
                busy_text=_("Adding repository…"),
            )

        self._input_dialog(
            _("Add Repository"),
            _("Describe the repository. The identifier may only contain letters, digits, "
              "dots, dashes and underscores."),
            [id_row, name_row, url_row, gpgcheck_row, key_row, enabled_row], _("Add"),
            is_valid, accept,
        )

    # -- Flatpak remotes --------------------------------------------------

    def _fill_remotes(self, remotes, error):
        self._clear(self.remote_group, self.remote_rows)
        if error:
            rows = [self._error_row(error)]
        elif not remotes:
            empty = Adw.ActionRow(title=_("No Flatpak remotes configured"),
                                  subtitle=_("Add Flathub to install Flatpak applications."))
            rows = [empty]
        else:
            rows = [self._remote_row(remote) for remote in remotes]
        for row in rows:
            self.remote_rows.append(row)
            self.remote_group.add(row)

    def _remote_row(self, remote):
        row = Adw.ActionRow(title=remote.title, subtitle=remote.name, use_markup=False)
        row.set_subtitle_lines(1)
        row.set_tooltip_text(_("URL: {url}").format(url=remote.url) if remote.url else None)
        row.add_suffix(tag(installation_label(remote.installation), remote.installation,
                           tooltip=_("Installation")))
        row.add_suffix(_switch(remote.enabled,
                               lambda sw, on, r=remote: self._toggle_remote(sw, r, on)))
        row.add_suffix(_trash_button(_("Remove Remote"), True,
                                     lambda r=remote: self._remove_remote(r)))
        return row

    def _toggle_remote(self, switch, remote, enabled):
        if enabled:
            done, heading, busy = (_("{name} enabled"), _("Could Not Enable Remote"),
                                   _("Enabling {name}…"))
        else:
            done, heading, busy = (_("{name} disabled"), _("Could Not Disable Remote"),
                                   _("Disabling {name}…"))
        self.window.run_task(
            backend.set_flatpak_remote_enabled, (remote, enabled),
            on_success=lambda _o: self.window.toast(done.format(name=remote.title)),
            error_heading=heading,
            busy_text=busy.format(name=remote.title),
            on_error=lambda: revert(switch),
        )

    def _remove_remote(self, remote):
        self._confirm(
            _("Remove Flatpak Remote?"),
            _("“{name}” ({installation}) will be removed. Applications installed from this "
              "remote must be uninstalled first.").format(
                name=remote.title, installation=installation_label(remote.installation)),
            _("Remove"),
            lambda: self.window.run_task(
                backend.remove_flatpak_remote, (remote,),
                on_success=lambda _o: self._changed(
                    _("{name} removed").format(name=remote.title)),
                error_heading=_("Could Not Remove Remote"),
                busy_text=_("Removing {name}…").format(name=remote.title),
            ),
        )

    def _add_remote(self):
        # Flathub is pre-filled because it is by far the most common remote.
        name_row = Adw.EntryRow(title=_("Name"), text="flathub")
        url_row = Adw.EntryRow(title=_(".flatpakrepo URL"), input_purpose=Gtk.InputPurpose.URL,
                               text=FLATHUB_URL)
        install_row = Adw.ComboRow(title=_("Installation"),
                                   model=Gtk.StringList.new([_("System"), _("User")]))

        def accept():
            installation = "system" if install_row.get_selected() == 0 else "user"
            self.window.run_task(
                backend.add_flatpak_remote,
                (name_row.get_text().strip(), url_row.get_text().strip(), installation),
                on_success=lambda _o: self._changed(_("Flatpak remote added")),
                error_heading=_("Could Not Add Remote"),
                busy_text=_("Adding Flatpak remote…"),
            )

        self._input_dialog(
            _("Add Flatpak Remote"),
            _("Flathub is filled in. To add another remote, replace the name and the URL "
              "with the ones given by its provider."),
            [name_row, url_row, install_row], _("Add"),
            lambda: bool(backend.REMOTE_NAME_RE.match(name_row.get_text().strip()))
            and backend.is_valid_url(url_row.get_text()),
            accept,
        )

    # -- Shared -----------------------------------------------------------

    def _confirm(self, heading, body, action_label, on_accept):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", action_label)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "ok" and on_accept())
        dialog.present(self.window)

    def _changed(self, message):
        self.window.toast(message)
        self.reload()
