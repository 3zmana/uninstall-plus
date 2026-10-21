"""Tests for language selection, translations and the settings store."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from uninstall_plus import i18n  # noqa: E402
from uninstall_plus.settings import Settings  # noqa: E402


class SystemLanguageTests(unittest.TestCase):
    def test_french_session(self):
        self.assertEqual(i18n.system_language({"LANG": "fr_FR.UTF-8"}), "fr")
        self.assertEqual(i18n.system_language({"LC_ALL": "fr_CA.UTF-8@euro"}), "fr")

    def test_unsupported_language_falls_back_to_english(self):
        self.assertEqual(i18n.system_language({"LANG": "de_DE.UTF-8"}), "en")
        self.assertEqual(i18n.system_language({"LANG": "C.UTF-8"}), "en")
        self.assertEqual(i18n.system_language({}), "en")

    def test_language_list_is_searched_in_order(self):
        self.assertEqual(i18n.system_language({"LANGUAGE": "de:fr", "LANG": "de_DE"}), "fr")
        self.assertEqual(i18n.system_language({"LANGUAGE": "de:es", "LANG": "fr_FR"}), "fr")

    def test_explicit_choice_wins(self):
        self.assertEqual(i18n.resolve_language("en"), "en")
        self.assertEqual(i18n.resolve_language("fr"), "fr")


@unittest.skipUnless(shutil.which("msgfmt"), "msgfmt is not installed")
class TranslationTests(unittest.TestCase):
    """Compile po/fr.po into a temporary locale directory and switch to it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        target = os.path.join(cls.tmp.name, "fr", "LC_MESSAGES")
        os.makedirs(target)
        subprocess.run(["msgfmt", "--check", "-o", os.path.join(target, "uninstall-plus.mo"),
                        os.path.join(ROOT, "po", "fr.po")], check=True)
        cls.saved = (i18n.LOCALEDIR, os.environ.get("LANGUAGE"))
        i18n.LOCALEDIR = cls.tmp.name

    @classmethod
    def tearDownClass(cls):
        i18n.LOCALEDIR = cls.saved[0]
        if cls.saved[1] is None:
            os.environ.pop("LANGUAGE", None)
        else:
            os.environ["LANGUAGE"] = cls.saved[1]
        i18n.set_language("en")
        cls.tmp.cleanup()

    def test_switching_languages(self):
        self.assertEqual(i18n.set_language("fr"), "fr")
        self.assertEqual(i18n._("Preferences"), "Paramètres")
        self.assertEqual(i18n._("Uninstall {name}?").format(name="vim"), "Désinstaller vim ?")
        self.assertEqual(os.environ["LANGUAGE"], "fr")
        self.assertEqual(i18n.set_language("en"), "en")
        self.assertEqual(i18n._("Preferences"), "Preferences")

    def test_plurals(self):
        i18n.set_language("fr")
        self.assertEqual(i18n.ngettext("{total} package", "{total} packages", 1).format(total=1),
                         "1 paquet")
        self.assertEqual(i18n.ngettext("{total} package", "{total} packages", 2).format(total=2),
                         "2 paquets")

    def test_sizes(self):
        i18n.set_language("fr")
        self.assertEqual(i18n.format_size(41_200_000), "41,2 Mo")
        self.assertEqual(i18n.localize_size_text("3.1 MB"), "3,1 Mo")
        i18n.set_language("en")
        self.assertEqual(i18n.format_size(41_200_000), "41.2 MB")
        self.assertEqual(i18n.format_size(512), "512 B")
        self.assertEqual(i18n.localize_size_text("not a size"), "not a size")

    def test_backend_messages_are_translated(self):
        from uninstall_plus import backend
        i18n.set_language("fr")
        self.assertEqual(backend.pretty_repo_name("@commandline"), "Fichier local")
        i18n.set_language("en")
        self.assertEqual(backend.pretty_repo_name("@commandline"), "Local File")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_and_persistence(self):
        settings = Settings(self.path)
        self.assertEqual(settings.get("language"), "system")
        self.assertEqual(settings.get("theme"), "system")
        settings.set("language", "fr")
        settings.set("theme", "dark")
        again = Settings(self.path)
        self.assertEqual(again.get("language"), "fr")
        self.assertEqual(again.get("theme"), "dark")

    def test_invalid_values_are_rejected_or_ignored(self):
        settings = Settings(self.path)
        with self.assertRaises(ValueError):
            settings.set("theme", "purple")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write('{"language": "klingon", "theme": "light"}')
        settings = Settings(self.path)
        self.assertEqual(settings.get("language"), "system")
        self.assertEqual(settings.get("theme"), "light")

    def test_broken_file_gives_defaults(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("not json")
        self.assertEqual(Settings(self.path).get("theme"), "system")


if __name__ == "__main__":
    unittest.main()
