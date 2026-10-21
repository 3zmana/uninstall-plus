"""Preferences dialog: interface language and color theme."""

from gi.repository import Adw, Gtk

from .i18n import SUPPORTED_LANGUAGES, _

# Order of the entries in the combo rows; must match the labels below.
LANGUAGE_VALUES = ["system"] + list(SUPPORTED_LANGUAGES)
THEME_VALUES = ["system", "light", "dark"]


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, app):
        super().__init__(title=_("Preferences"))
        self.app = app
        self.set_search_enabled(False)

        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
        self.add(page)

        # Language. Language names are written in their own language so that
        # anyone can find theirs, whatever the current interface language.
        # The explanations go in the group description: as a row subtitle they
        # would squeeze the selected value of the combo row.
        language_group = Adw.PreferencesGroup(
            title=_("Language"),
            description=_("“System” uses the language of your session, or English when it is "
                          "not available."),
        )
        language_labels = [_("System")] + list(SUPPORTED_LANGUAGES.values())
        self.language_row = Adw.ComboRow(
            title=_("Interface Language"),
            model=Gtk.StringList.new(language_labels),
        )
        self.language_row.set_selected(LANGUAGE_VALUES.index(app.settings.get("language")))
        self.language_row.connect("notify::selected", self._on_language_changed)
        language_group.add(self.language_row)
        page.add(language_group)

        # Appearance.
        appearance_group = Adw.PreferencesGroup(
            title=_("Appearance"),
            description=_("“System” follows the light or dark style chosen in GNOME Settings."),
        )
        self.theme_row = Adw.ComboRow(
            title=_("Theme"),
            model=Gtk.StringList.new([_("System"), _("Light"), _("Dark")]),
        )
        self.theme_row.set_selected(THEME_VALUES.index(app.settings.get("theme")))
        self.theme_row.connect("notify::selected", self._on_theme_changed)
        appearance_group.add(self.theme_row)
        page.add(appearance_group)

    def _on_language_changed(self, row, _pspec):
        self.app.change_language(LANGUAGE_VALUES[row.get_selected()])

    def _on_theme_changed(self, row, _pspec):
        self.app.change_theme(THEME_VALUES[row.get_selected()])
