"""Translations for Uninstall Plus.

Strings are translated with gettext. The language can be chosen in the
Preferences dialog ("system", "en" or "fr"); "system" picks the first
supported language of the user session and falls back to English.

This module only uses the standard library so the backend and the tests can
import it without GTK.
"""

import gettext
import locale
import os

DOMAIN = "uninstall-plus"
LOCALEDIR = os.environ.get("UNINSTALL_PLUS_LOCALEDIR", "/usr/share/locale")

# Languages with a translation, shown in their own language in the settings.
SUPPORTED_LANGUAGES = {"en": "English", "fr": "Français"}
DEFAULT_LANGUAGE = "en"

# Environment as it was when the application started, before we change
# LANGUAGE to apply the user's choice. Used to resolve the "system" choice.
_ORIGINAL_ENV = {
    name: os.environ.get(name, "")
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")
}

_translation = gettext.NullTranslations()
_current = DEFAULT_LANGUAGE


def _(message):
    """Translate a message into the current language."""
    return _translation.gettext(message)


def ngettext(singular, plural, count):
    """Translate a message that depends on a number."""
    return _translation.ngettext(singular, plural, count)


def N_(message):
    """Mark a string for translation without translating it yet."""
    return message


def _language_code(value):
    """"fr_FR.UTF-8@euro" -> "fr"."""
    return value.split(".")[0].split("@")[0].split("_")[0].strip().lower()


def system_language(env=None):
    """First supported language of the session, or English.

    LANGUAGE may hold a list of preferred languages ("de:fr"); otherwise the
    first set variable among LC_ALL, LC_MESSAGES and LANG decides.
    """
    env = _ORIGINAL_ENV if env is None else env
    candidates = [part for part in env.get("LANGUAGE", "").split(":") if part]
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if env.get(name):
            candidates.append(env[name])
            break
    for candidate in candidates:
        code = _language_code(candidate)
        if code in SUPPORTED_LANGUAGES:
            return code
    return DEFAULT_LANGUAGE


def resolve_language(choice):
    """Turn a setting value ("system", "en", "fr"...) into a language code."""
    if choice in SUPPORTED_LANGUAGES:
        return choice
    return system_language()


def set_language(choice):
    """Apply a language setting and return the language code now in use."""
    global _translation, _current
    language = resolve_language(choice)
    _current = language
    _translation = gettext.translation(
        DOMAIN, LOCALEDIR, languages=[language], fallback=True
    )
    # GTK, libadwaita and GLib translate their own strings (dialog buttons,
    # sizes...) with the C gettext, which follows LANGUAGE. Point it at the
    # same language so the whole window speaks one language.
    os.environ["LANGUAGE"] = language
    try:
        # Re-applying the current locale makes glibc forget cached translations.
        locale.setlocale(locale.LC_MESSAGES, locale.setlocale(locale.LC_MESSAGES))
    except (locale.Error, AttributeError):
        pass
    return language


def current_language():
    return _current


def format_size(size):
    """Human readable size in the current language (decimal units)."""
    units = [_("B"), _("kB"), _("MB"), _("GB"), _("TB")]
    value = float(size)
    index = 0
    while value >= 1000 and index < len(units) - 1:
        value /= 1000
        index += 1
    number = f"{int(value)}" if index == 0 else f"{value:.1f}"
    if _current == "fr":
        number = number.replace(".", ",")
    # Translators: a file size, for example "12.3 MB".
    return _("{number} {unit}").format(number=number, unit=units[index])


_SIZE_UNITS = {"bytes": 1, "B": 1, "kB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}


def localize_size_text(text):
    """Re-format a size printed by a command ("41.2 MB") in the current language."""
    parts = text.replace(" ", " ").split()
    if len(parts) == 2 and parts[1] in _SIZE_UNITS:
        try:
            return format_size(float(parts[0].replace(",", ".")) * _SIZE_UNITS[parts[1]])
        except ValueError:
            pass
    return text
