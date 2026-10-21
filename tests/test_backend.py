"""Unit tests for the parsing code of the backend (no GTK required)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from uninstall_plus import backend  # noqa: E402

S = backend.FIELD_SEP
R = backend.RECORD_SEP


class RpmParsingTests(unittest.TestCase):
    def test_parse_rpm_list_skips_keys_and_sorts(self):
        output = "\n".join([
            S.join(["zsh", "5.9-1.fc44", "x86_64", "Powerful shell", "1000", "1700000000"]),
            S.join(["gpg-pubkey", "abc-def", "(none)", "key", "0", "0"]),
            S.join(["Bash", "5.2-1.fc44", "x86_64", "(none)", "(none)", "1700000001"]),
            "garbage line",
        ])
        packages = backend.parse_rpm_list(output)
        self.assertEqual([p.name for p in packages], ["Bash", "zsh"])
        self.assertEqual(packages[0].summary, "")
        self.assertEqual(packages[0].size, 0)
        self.assertEqual(packages[1].nevra, "zsh-5.9-1.fc44.x86_64")
        self.assertEqual(packages[1].key, "zsh.x86_64")

    def test_parse_records_with_and_without_newlines(self):
        expanded = f"a.x86_64{S}fedora{R}\nb.noarch{S}updates{R}\n"
        literal = f"a.x86_64{S}fedora{R}\\nb.noarch{S}updates{R}\\n"
        expected = [["a.x86_64", "fedora"], ["b.noarch", "updates"]]
        self.assertEqual(backend.parse_records(expanded), expected)
        self.assertEqual(backend.parse_records(literal), expected)

    def test_list_rpm_packages_merges_dnf_data(self):
        rpm_out = "\n".join([
            S.join(["vim-enhanced", "9.1-1.fc44", "x86_64", "Vim", "10", "1"]),
            S.join(["glibc", "2.41-1.fc44", "x86_64", "C library", "20", "1"]),
        ])
        installed = f"vim-enhanced.x86_64{S}updates{R}\nglibc.x86_64{S}<unknown>{R}\n"
        userinstalled = f"vim-enhanced.x86_64{R}\n"

        def fake_run(args, timeout=None):
            if args[0] == "rpm":
                return 0, rpm_out, ""
            if "--disablerepo=*" in args:
                return 1, "", "Unknown argument"  # exercise the retry path
            if "--userinstalled" in args:
                return 0, userinstalled, ""
            return 0, installed, ""

        with mock.patch.object(backend, "run_command", side_effect=fake_run):
            result = backend.list_rpm_packages()
        by_name = {p.name: p for p in result.packages}
        self.assertEqual(by_name["vim-enhanced"].repo, "updates")
        self.assertTrue(by_name["vim-enhanced"].user_installed)
        self.assertEqual(by_name["glibc"].repo, "")
        self.assertFalse(by_name["glibc"].user_installed)
        self.assertTrue(result.repo_info_available)

    def test_list_rpm_packages_survives_dnf_failure(self):
        rpm_out = S.join(["vim", "9-1", "x86_64", "Vim", "1", "1"])

        def fake_run(args, timeout=None):
            if args[0] == "rpm":
                return 0, rpm_out, ""
            return 1, "", "boom"

        with mock.patch.object(backend, "run_command", side_effect=fake_run):
            result = backend.list_rpm_packages()
        self.assertEqual(len(result.packages), 1)
        self.assertFalse(result.repo_info_available)
        self.assertFalse(result.user_info_available)
        self.assertIn("boom", result.warning)

    def test_pretty_repo_names(self):
        self.assertEqual(backend.pretty_repo_name(""), "Unknown")
        self.assertEqual(backend.pretty_repo_name("@commandline"), "Local File")
        self.assertEqual(
            backend.pretty_repo_name("copr:copr.fedorainfracloud.org:timlau:yumex-ng"),
            "COPR: timlau/yumex-ng",
        )
        self.assertEqual(
            backend.pretty_repo_name("copr:copr.fedorainfracloud.org:group_kde:stable"),
            "COPR: @kde/stable",
        )
        self.assertEqual(backend.pretty_repo_name("updates"), "updates")


class FlatpakParsingTests(unittest.TestCase):
    def test_parse_flatpak_list(self):
        output = "\n".join([
            "Application ID\tName\tDescription\tVersion\tBranch\tOrigin\tInstallation\tSize\tRef",
            "org.gnome.Boxes\tBoxes\tVirtualization made simple\t49.0\tstable\tflathub\tsystem\t12.3 MB\t"
            "app/org.gnome.Boxes/x86_64/stable",
            "com.example.App\t\tNo name\t1.0\tstable\tflathub\tuser\t1 MB\tapp/com.example.App/x86_64/stable",
        ])
        apps = backend.parse_flatpak_list(output)
        self.assertEqual([a.app_id for a in apps], ["org.gnome.Boxes", "com.example.App"])
        self.assertEqual(apps[1].name, "com.example.App")
        self.assertEqual(apps[0].installation, "system")

    def test_installation_flags(self):
        self.assertEqual(backend.installation_flag("system"), "--system")
        self.assertEqual(backend.installation_flag("user"), "--user")
        self.assertEqual(backend.installation_flag("extra"), "--installation=extra")

    def test_parse_flatpak_remotes(self):
        output = "\n".join([
            "flathub\tFlathub\thttps://dl.flathub.org/repo/\tsystem",
            "fedora\tFedora Flatpaks\toci+https://registry.fedoraproject.org\tsystem,oci,disabled",
            "flathub\tFlathub\thttps://dl.flathub.org/repo/\tuser",
        ])
        remotes = backend.parse_flatpak_remotes(output)
        self.assertEqual(len(remotes), 3)
        fedora = [r for r in remotes if r.name == "fedora"][0]
        self.assertFalse(fedora.enabled)
        self.assertEqual({r.installation for r in remotes if r.name == "flathub"}, {"system", "user"})

    def test_uninstall_flatpak_runs_unused_cleanup(self):
        app = backend.FlatpakApp("org.x.Y", "Y", "", "1", "stable", "flathub", "user", "1 MB",
                                 "app/org.x.Y/x86_64/stable")
        calls = []

        def fake_run(args, timeout=None):
            calls.append(args)
            return 0, "", ""

        with mock.patch.object(backend, "run_command", side_effect=fake_run):
            backend.uninstall_flatpak(app, delete_data=True)
        self.assertEqual(calls[0], ["flatpak", "uninstall", "-y", "--user", "--delete-data",
                                    "app/org.x.Y/x86_64/stable"])
        self.assertEqual(calls[1], ["flatpak", "uninstall", "-y", "--unused", "--user"])


class RepoParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_dir = os.path.join(self.tmp.name, "yum.repos.d")
        self.override_dir = os.path.join(self.tmp.name, "repos.override.d")
        os.makedirs(self.repo_dir)
        os.makedirs(self.override_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, directory, name, content):
        with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
            handle.write(content)

    def test_repos_with_vars_overrides_and_copr(self):
        self.write(self.repo_dir, "fedora.repo", (
            "[fedora]\nname=Fedora $releasever - $basearch\n"
            "metalink=https://mirrors.fedoraproject.org/metalink?repo=fedora-$releasever&arch=$basearch\n"
            "enabled=1\n\n"
            "[fedora-debuginfo]\nname=Fedora $releasever - Debug\nenabled=0\n"
            "baseurl=https://example.org/debug/\n         https://mirror.example.org/debug/\n"
        ))
        self.write(self.repo_dir, "_copr:copr.fedorainfracloud.org:timlau:yumex-ng.repo", (
            "[copr:copr.fedorainfracloud.org:timlau:yumex-ng]\nname=Copr repo for yumex-ng\n"
            "baseurl=https://download.copr.fedorainfracloud.org/results/timlau/yumex-ng/fedora-$releasever-$basearch/\n"
            "enabled=1\n"
        ))
        self.write(self.override_dir, "99-config_manager.repo", "[fedora]\nenabled=0\n[*-debuginfo]\nenabled=1\n")

        variables = {"releasever": "44", "basearch": "x86_64"}
        repos = backend.parse_repo_dirs([self.repo_dir], [self.override_dir], variables)
        by_id = {r.repo_id: r for r in repos}

        self.assertEqual(by_id["fedora"].name, "Fedora 44 - x86_64")
        self.assertFalse(by_id["fedora"].enabled)
        self.assertTrue(by_id["fedora-debuginfo"].enabled)
        self.assertTrue(by_id["fedora-debuginfo"].is_debug_or_source)
        self.assertEqual(by_id["fedora-debuginfo"].url, "https://example.org/debug/")
        copr = by_id["copr:copr.fedorainfracloud.org:timlau:yumex-ng"]
        self.assertEqual(copr.copr, "timlau/yumex-ng")
        self.assertIn("fedora-44-x86_64", copr.url)
        # COPR repositories are listed after the regular ones.
        self.assertEqual(repos[-1].repo_id, copr.repo_id)

    def test_broken_repo_file_is_ignored(self):
        self.write(self.repo_dir, "broken.repo", "this is not ini\n[[[")
        self.write(self.repo_dir, "good.repo", "[good]\nname=Good\nbaseurl=https://x/\n")
        repos = backend.parse_repo_dirs([self.repo_dir], [], {})
        self.assertEqual([r.repo_id for r in repos], ["good"])

    def test_vars_and_validation(self):
        self.assertEqual(backend.substitute_vars("a-${releasever}-$basearch-$nope",
                                                 {"releasever": "44", "basearch": "aarch64"}),
                         "a-44-aarch64-$nope")
        self.assertTrue(backend.is_valid_url("https://example.org/x.repo"))
        self.assertFalse(backend.is_valid_url("example.org/x.repo"))
        self.assertTrue(backend.COPR_RE.match("timlau/yumex-ng"))
        self.assertTrue(backend.COPR_RE.match("@kde/stable"))
        self.assertFalse(backend.COPR_RE.match("timlau"))


class HelperTests(unittest.TestCase):
    """Test the helper's pure functions by loading it as a module."""

    @classmethod
    def setUpClass(cls):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(__file__), "..", "helper", "uninstall-plus-helper")
        loader = importlib.machinery.SourceFileLoader("helper", path)
        spec = importlib.util.spec_from_loader("helper", loader)
        cls.helper = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.helper)

    def test_without_section(self):
        lines = ["[a]\n", "x=1\n", "\n", "[b]\n", "y=2\n", "[c]\n", "z=3\n"]
        self.assertEqual(self.helper.without_section(lines, "b"),
                         ["[a]\n", "x=1\n", "\n", "[c]\n", "z=3\n"])
        self.assertEqual(self.helper.sections_in(lines), ["a", "b", "c"])

    def test_copr_spec(self):
        self.assertEqual(
            self.helper.copr_spec_from_file("/etc/yum.repos.d/_copr:copr.fedorainfracloud.org:timlau:yumex-ng.repo"),
            "copr.fedorainfracloud.org/timlau/yumex-ng",
        )
        self.assertEqual(self.helper.copr_spec_from_file("/etc/yum.repos.d/fedora.repo"), "")

    def test_argument_patterns_reject_options(self):
        self.assertIsNone(self.helper.PACKAGE_RE.fullmatch("--all"))
        self.assertIsNone(self.helper.PACKAGE_RE.fullmatch("vim; rm -rf /"))
        self.assertIsNotNone(self.helper.PACKAGE_RE.fullmatch("vim-enhanced-2:9.1.0-1.fc44.x86_64"))
        self.assertIsNotNone(self.helper.PACKAGE_RE.fullmatch("libstdc++-15.1-1.fc44.x86_64"))
        self.assertIsNone(self.helper.REPO_ID_RE.fullmatch("../etc/passwd"))
        self.assertIsNone(self.helper.URL_RE.fullmatch("https://x/\nevil"))
        self.assertIsNone(self.helper.NAME_RE.fullmatch("bad\nname"))

    def test_language_flag_is_accepted_and_validated(self):
        # A valid flag is consumed before the action is parsed.
        with self.assertRaises(SystemExit) as ctx:
            self.helper.main(["helper", "--lang=fr", "remove"])
        self.assertEqual(ctx.exception.code, 2)
        # A malformed language is ignored instead of being passed to gettext.
        with self.assertRaises(SystemExit) as ctx:
            self.helper.main(["helper", "--lang=../../etc", "remove"])
        self.assertEqual(ctx.exception.code, 2)

    def test_run_helper_passes_language_and_detects_cancel(self):
        calls = []

        def fake_run(args, timeout=None):
            calls.append(args)
            return backend.PKEXEC_DISMISSED, "", ""

        with mock.patch.object(backend, "run_command", side_effect=fake_run), \
                mock.patch.object(backend.os.path, "exists", return_value=True):
            with self.assertRaises(backend.AuthenticationCancelled):
                backend.remove_rpm("vim-9-1.x86_64")
        self.assertEqual(calls[0][0], "pkexec")
        self.assertTrue(calls[0][2].startswith("--lang="))
        self.assertEqual(calls[0][3:], ["remove", "vim-9-1.x86_64"])

    def test_main_rejects_unknown_action_and_bad_arity(self):
        with self.assertRaises(SystemExit) as ctx:
            self.helper.main(["helper", "format-disk"])
        self.assertEqual(ctx.exception.code, 2)
        with self.assertRaises(SystemExit) as ctx:
            self.helper.main(["helper", "remove"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
