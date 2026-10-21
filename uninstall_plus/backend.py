"""Backend for Uninstall Plus.

This module only uses the Python standard library so it can be unit tested
without GTK. It reads installed software and repositories without any
privileges, and delegates every system change either to Flatpak (which asks
for authentication through polkit on its own) or to the privileged helper,
which is started through pkexec.
"""

import configparser
import fnmatch
import glob
import os
import platform
import re
import subprocess
from dataclasses import dataclass, field

from .i18n import _, current_language

# Path of the privileged helper. Can be overridden for development.
HELPER_PATH = os.environ.get(
    "UNINSTALL_PLUS_HELPER", "/usr/libexec/uninstall-plus/uninstall-plus-helper"
)

# Separators used in query formats. They are unlikely to appear in package data.
FIELD_SEP = "|#|"
RECORD_SEP = "@@@"

# Directories scanned for DNF repository definitions (dnf5 defaults).
REPO_DIRS = ["/etc/yum.repos.d", "/etc/distro.repos.d", "/usr/share/dnf5/repos.d"]
# Directories holding repository overrides written by "dnf config-manager setopt".
OVERRIDE_DIRS = ["/usr/share/dnf5/repos.override.d", "/etc/dnf/repos.override.d"]
# Directories holding DNF variable definitions ($releasever, custom vars...).
VARS_DIRS = ["/usr/share/dnf5/vars.d", "/etc/dnf/vars"]
# The only directory where the helper adds or removes repository files.
USER_REPO_DIR = "/etc/yum.repos.d"

# Exit codes returned by pkexec itself.
PKEXEC_DISMISSED = 126
PKEXEC_NOT_AUTHORIZED = 127


class BackendError(Exception):
    """Raised when an external command fails. `details` holds raw output."""

    def __init__(self, message, details=""):
        super().__init__(message)
        self.details = details.strip() if details else ""


class AuthenticationCancelled(BackendError):
    """The user closed the password prompt; not worth an error dialog."""


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def run_command(args, timeout=None):
    """Run a command without a shell and return (returncode, stdout, stderr)."""
    env = dict(os.environ)
    # Force English, uncoloured output so it can be parsed and shown as-is.
    env["LC_ALL"] = "C.UTF-8"
    env["NO_COLOR"] = "1"
    try:
        proc = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise BackendError(_("The command “{command}” is not installed.").format(command=args[0])) from exc
    except subprocess.TimeoutExpired as exc:
        raise BackendError(
            _("The command “{command}” took too long to answer.").format(command=args[0])
        ) from exc
    return proc.returncode, proc.stdout, proc.stderr


def run_helper(*args):
    """Run the privileged helper through pkexec and return its stdout.

    Raises BackendError when authentication is refused or the action fails.
    """
    if not os.path.exists(HELPER_PATH):
        raise BackendError(
            _("The privileged helper is missing."),
            _("Expected location: {path}\nReinstall the uninstall-plus package.").format(
                path=HELPER_PATH),
        )
    # pkexec clears the environment, so the helper is told which language to use.
    code, out, err = run_command(["pkexec", HELPER_PATH, f"--lang={current_language()}", *args])
    if code == PKEXEC_DISMISSED:
        raise AuthenticationCancelled(_("Authentication was cancelled."))
    if code == PKEXEC_NOT_AUTHORIZED:
        raise BackendError(_("You are not allowed to perform this action."), err)
    if code != 0:
        raise BackendError(_first_error_line(err, out) or _("The operation failed."), _join(out, err))
    return out


def _join(*parts):
    return "\n".join(p.strip() for p in parts if p and p.strip())


def _first_error_line(*texts):
    """Return the most meaningful error line from command output."""
    for text in texts:
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith(("error", "problem", "failed", "cannot", "can't")):
                return line
    for text in texts:
        for line in reversed((text or "").splitlines()):
            if line.strip():
                return line.strip()
    return ""


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# RPM packages
# ---------------------------------------------------------------------------

@dataclass
class RpmPackage:
    """An installed RPM package."""

    name: str
    version: str  # "version-release"
    arch: str
    summary: str
    size: int = 0  # installed size in bytes
    install_time: int = 0  # Unix timestamp
    repo: str = ""  # repository id the package came from ("" when unknown)
    user_installed: bool = False

    @property
    def nevra(self):
        """Name-version-release.arch, as understood by rpm and dnf."""
        if self.arch and self.arch != "(none)":
            return f"{self.name}-{self.version}.{self.arch}"
        return f"{self.name}-{self.version}"

    @property
    def key(self):
        """Identifier used to match rpm output with dnf output."""
        return f"{self.name}.{self.arch}"


@dataclass
class RpmListResult:
    """Result of list_rpm_packages()."""

    packages: list
    repo_info_available: bool = True
    user_info_available: bool = True
    warning: str = ""


RPM_LIST_QUERYFORMAT = FIELD_SEP.join(
    ["%{NAME}", "%{VERSION}-%{RELEASE}", "%{ARCH}", "%{SUMMARY}", "%{SIZE}", "%{INSTALLTIME}"]
) + "\\n"


def parse_rpm_list(output):
    """Parse the output of `rpm -qa --qf RPM_LIST_QUERYFORMAT`."""
    packages = []
    for line in output.splitlines():
        parts = line.split(FIELD_SEP)
        if len(parts) != 6:
            continue
        name, version, arch, summary, size, install_time = (p.strip() for p in parts)
        # GPG public keys are stored as fake packages; they are not software.
        if not name or name == "gpg-pubkey":
            continue
        packages.append(
            RpmPackage(
                name=name,
                version=version,
                arch=arch,
                summary=summary if summary != "(none)" else "",
                size=_to_int(size),
                install_time=_to_int(install_time),
            )
        )
    packages.sort(key=lambda p: (p.name.lower(), p.version, p.arch))
    return packages


def parse_records(output):
    """Split dnf queryformat output into lists of fields.

    Each record ends with RECORD_SEP. This works whether or not dnf expands
    the trailing "\\n" escape, which differs between dnf versions.
    """
    records = []
    for record in output.split(RECORD_SEP):
        record = record.replace("\\n", "").strip()
        if record:
            records.append([f.strip() for f in record.split(FIELD_SEP)])
    return records


def _dnf_repoquery(extra_args, queryformat):
    """Run `dnf repoquery` on installed packages and return parsed records.

    Remote repositories are disabled first because the information we need
    (origin repository, install reason) lives in the local system state. If
    this option is refused by the installed dnf, the query is retried with
    the default repository configuration.
    """
    base = ["dnf", "-q", "repoquery", *extra_args, "--queryformat", queryformat]
    attempts = [base[:2] + ["--disablerepo=*"] + base[2:], base]
    last_error = ""
    for args in attempts:
        code, out, err = run_command(args, timeout=300)
        if code == 0:
            return parse_records(out)
        last_error = _join(out, err)
    raise BackendError(_("Could not query DNF."), last_error)


def list_rpm_packages():
    """Return every installed RPM with its origin repository."""
    code, out, err = run_command(["rpm", "-qa", "--qf", RPM_LIST_QUERYFORMAT], timeout=120)
    if code != 0:
        raise BackendError(_("Could not read the RPM database."), err)
    result = RpmListResult(packages=parse_rpm_list(out))
    warnings = []

    # Origin repository of every installed package.
    try:
        records = _dnf_repoquery(
            ["--installed"], f"%{{name}}.%{{arch}}{FIELD_SEP}%{{from_repo}}{RECORD_SEP}\\n"
        )
        repos = {r[0]: r[1] for r in records if len(r) >= 2}
        for pkg in result.packages:
            repo = repos.get(pkg.key, "")
            pkg.repo = "" if repo in ("<unknown>", "(none)") else repo
    except BackendError as exc:
        result.repo_info_available = False
        warnings.append(_("Repository information is unavailable: {error}").format(
            error=exc.details or exc))

    # Packages explicitly installed by the user (not pulled in as dependencies).
    try:
        records = _dnf_repoquery(["--userinstalled"], f"%{{name}}.%{{arch}}{RECORD_SEP}\\n")
        user_keys = {r[0] for r in records if r}
        for pkg in result.packages:
            pkg.user_installed = pkg.key in user_keys
    except BackendError as exc:
        result.user_info_available = False
        warnings.append(_("Install reason is unavailable: {error}").format(
            error=exc.details or exc))

    result.warning = "\n".join(warnings)
    return result


@dataclass
class RpmDetails:
    """Extra information shown in the package details dialog."""

    description: str = ""
    url: str = ""
    license: str = ""
    vendor: str = ""
    source_rpm: str = ""


def get_rpm_details(nevra):
    """Return the long description and metadata of one installed package."""
    qf = FIELD_SEP.join(["%{DESCRIPTION}", "%{URL}", "%{LICENSE}", "%{VENDOR}", "%{SOURCERPM}"])
    code, out, err = run_command(["rpm", "-q", "--qf", qf + RECORD_SEP, nevra], timeout=60)
    if code != 0:
        raise BackendError(_("Could not read package details."), err)
    first = out.split(RECORD_SEP)[0]
    parts = [p.strip() for p in first.split(FIELD_SEP)]
    parts += [""] * (5 - len(parts))
    clean = ["" if p == "(none)" else p for p in parts[:5]]
    return RpmDetails(*clean)


def preview_rpm_removal(nevra):
    """Return the DNF transaction text describing what removal would do."""
    return run_helper("remove-preview", nevra).strip()


def clean_transaction_text(text):
    """Drop DNF progress lines that come before the transaction table."""
    # The preview runs with --assumeno, so DNF always reports an abort at the end.
    lines = [line for line in text.splitlines()
             if not re.match(r"^\s*(Operation aborted|Is this ok)", line)]
    for index, line in enumerate(lines):
        if re.match(r"^\s*(Package\s|Removing)", line):
            return "\n".join(lines[index:]).strip()
    return "\n".join(lines).strip()


def removal_count(text):
    """Number of packages a DNF removal transaction removes, or 0 if unknown."""
    total = 0
    # dnf5 summary: " Removing:   3 packages" (dependents are listed separately).
    for match in re.finditer(r"^\s*Removing(?: [a-z ]+)?:\s+(\d+) packages?", text, re.MULTILINE):
        total += int(match.group(1))
    if total:
        return total
    # dnf4 summary: "Remove  3 Packages".
    match = re.search(r"^\s*Remove\s+(\d+)\s+Packages?", text, re.MULTILINE)
    return int(match.group(1)) if match else 0


def remove_rpm(nevra):
    """Uninstall an RPM package (and the packages that depend on it)."""
    return run_helper("remove", nevra)


def pretty_repo_name(repo_id):
    """Human readable label for a repository id from `from_repo`."""
    if not repo_id:
        return _("Unknown")
    if repo_id == "@commandline":
        return _("Local File")
    if repo_id in ("anaconda", "@anaconda"):
        return _("System Installer")
    copr = parse_copr_repo_id(repo_id)
    if copr:
        return f"COPR: {copr}"
    return repo_id


def parse_copr_repo_id(repo_id):
    """Return "owner/project" for a COPR repo id, or None.

    COPR repo ids look like "copr:copr.fedorainfracloud.org:owner:project",
    with group projects using "group_name" as owner.
    """
    if not repo_id.startswith("copr:"):
        return None
    parts = repo_id.split(":")
    if len(parts) < 4:
        return None
    owner, project = parts[2], parts[3]
    if owner.startswith("group_"):
        owner = "@" + owner[len("group_"):]
    return f"{owner}/{project}"


# ---------------------------------------------------------------------------
# Flatpak applications
# ---------------------------------------------------------------------------

FLATPAK_COLUMNS = [
    "application", "name", "description", "version", "branch",
    "origin", "installation", "size", "ref",
]


@dataclass
class FlatpakApp:
    """An installed Flatpak application."""

    app_id: str
    name: str
    description: str
    version: str
    branch: str
    origin: str
    installation: str  # "system", "user" or a custom installation name
    size: str
    ref: str


def installation_flag(installation):
    """Command line flag selecting a Flatpak installation."""
    if installation == "user":
        return "--user"
    if installation in ("system", "", "default"):
        return "--system"
    return f"--installation={installation}"


def parse_flatpak_list(output):
    """Parse `flatpak list --app --columns=...` output (tab separated)."""
    apps = []
    for line in output.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < len(FLATPAK_COLUMNS) or parts[0] in ("Application ID", "Application"):
            continue
        app = FlatpakApp(*parts[: len(FLATPAK_COLUMNS)])
        if not app.name:
            app.name = app.app_id
        apps.append(app)
    apps.sort(key=lambda a: (a.name.lower(), a.installation))
    return apps


def list_flatpak_apps():
    """Return installed Flatpak applications (runtimes are not listed)."""
    code, out, err = run_command(
        ["flatpak", "list", "--app", "--columns=" + ",".join(FLATPAK_COLUMNS)], timeout=120
    )
    if code != 0:
        raise BackendError(_("Could not list Flatpak applications."), err)
    return parse_flatpak_list(out)


def uninstall_flatpak(app, delete_data=False):
    """Uninstall a Flatpak app, then remove the runtimes nobody uses anymore.

    Flatpak asks for authentication through polkit by itself when the app is
    installed system-wide.
    """
    flag = installation_flag(app.installation)
    args = ["flatpak", "uninstall", "-y", flag]
    if delete_data:
        args.append("--delete-data")
    args.append(app.ref or app.app_id)
    code, out, err = run_command(args, timeout=900)
    if code != 0:
        raise BackendError(_first_error_line(err, out) or _("Uninstall failed."), _join(out, err))
    # Clean up runtimes that are no longer needed by any application.
    run_command(["flatpak", "uninstall", "-y", "--unused", flag], timeout=900)
    return out


# ---------------------------------------------------------------------------
# DNF repositories
# ---------------------------------------------------------------------------

@dataclass
class DnfRepo:
    """A DNF repository definition."""

    repo_id: str
    name: str
    enabled: bool
    file_path: str
    url: str = ""
    owner_package: str = ""  # RPM providing the .repo file, if any
    copr: str = ""  # "owner/project" for COPR repositories

    @property
    def removable(self):
        """Only repositories added by the user can be removed."""
        return not self.owner_package and os.path.dirname(self.file_path) == USER_REPO_DIR

    @property
    def is_debug_or_source(self):
        rid = self.repo_id.lower()
        return rid.endswith(("-debuginfo", "-source", "-debug", "-src")) or "-debuginfo" in rid


def _parse_bool(value, default=True):
    if value is None:
        return default
    value = value.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


def load_dnf_vars(os_release_path="/etc/os-release", vars_dirs=None):
    """Return the variables DNF substitutes in repository files."""
    variables = {}
    machine = platform.machine() or "x86_64"
    variables["basearch"] = "i386" if re.fullmatch(r"i[3-6]86", machine) else machine
    variables["arch"] = machine
    try:
        with open(os_release_path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VERSION_ID="):
                    version = line.split("=", 1)[1].strip().strip('"')
                    variables["releasever"] = version
                    variables["releasever_major"] = version.split(".")[0]
    except OSError:
        pass
    for directory in vars_dirs if vars_dirs is not None else VARS_DIRS:
        for path in sorted(glob.glob(os.path.join(directory, "*"))):
            try:
                with open(path, encoding="utf-8") as handle:
                    variables[os.path.basename(path)] = handle.read().strip().splitlines()[0]
            except (OSError, IndexError, UnicodeDecodeError):
                continue
    return variables


def substitute_vars(text, variables):
    """Replace $var and ${var} with their values."""
    def replace(match):
        name = match.group(1) or match.group(2)
        return variables.get(name, match.group(0))

    return re.sub(r"\$\{(\w+)\}|\$(\w+)", replace, text)


def _read_ini(path):
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None
    return parser


def copr_spec_from_file(path):
    """Return "owner/project" when a .repo file was created by `dnf copr`."""
    base = os.path.basename(path)
    if not base.startswith("_copr") or not base.endswith(".repo"):
        return ""
    # File names look like "_copr:copr.fedorainfracloud.org:owner:project.repo".
    parts = base[len("_copr:"): -len(".repo")].split(":")
    if len(parts) < 3:
        return ""
    owner, project = parts[1], parts[2]
    if owner.startswith("group_"):
        owner = "@" + owner[len("group_"):]
    return f"{owner}/{project}"


def parse_repo_dirs(repo_dirs=None, override_dirs=None, variables=None):
    """Read repository definitions and apply enable/disable overrides."""
    repo_dirs = REPO_DIRS if repo_dirs is None else repo_dirs
    override_dirs = OVERRIDE_DIRS if override_dirs is None else override_dirs
    variables = load_dnf_vars() if variables is None else variables

    repos = {}
    for directory in repo_dirs:
        for path in sorted(glob.glob(os.path.join(directory, "*.repo"))):
            parser = _read_ini(path)
            if parser is None:
                continue
            for section in parser.sections():
                if section == "main" or section in repos:
                    continue
                get = lambda key: parser.get(section, key, fallback=None)  # noqa: E731
                url = get("baseurl") or get("metalink") or get("mirrorlist") or ""
                url = url.strip().split()[0] if url.strip() else ""
                repos[section] = DnfRepo(
                    repo_id=section,
                    name=substitute_vars(get("name") or section, variables),
                    enabled=_parse_bool(get("enabled"), default=True),
                    file_path=path,
                    url=substitute_vars(url, variables),
                    copr=copr_spec_from_file(path),
                )

    # Overrides (written by `dnf config-manager setopt`) may use glob patterns.
    for directory in override_dirs:
        for path in sorted(glob.glob(os.path.join(directory, "*.repo"))):
            parser = _read_ini(path)
            if parser is None:
                continue
            for pattern in parser.sections():
                value = parser.get(pattern, "enabled", fallback=None)
                if value is None:
                    continue
                for repo_id, repo in repos.items():
                    if fnmatch.fnmatchcase(repo_id, pattern):
                        repo.enabled = _parse_bool(value, default=repo.enabled)

    return sorted(repos.values(), key=lambda r: (r.copr != "", r.name.lower()))


def list_dnf_repos():
    """Return all DNF repositories, with the package owning each file."""
    repos = parse_repo_dirs()
    owners = {}
    for path in {r.file_path for r in repos}:
        code, out, _err = run_command(["rpm", "-qf", "--qf", "%{NAME}\\n", path], timeout=30)
        owners[path] = out.splitlines()[0].strip() if code == 0 and out.strip() else ""
    for repo in repos:
        repo.owner_package = owners.get(repo.file_path, "")
    return repos


def set_dnf_repo_enabled(repo_id, enabled):
    return run_helper("repo-enable" if enabled else "repo-disable", repo_id)


def add_dnf_repo_from_url(url):
    return run_helper("repo-add-url", url)


def add_dnf_repo_manual(repo_id, name, baseurl, gpgcheck, gpgkey, enabled):
    return run_helper(
        "repo-add-manual", repo_id, name, baseurl,
        "1" if gpgcheck else "0", gpgkey or "", "1" if enabled else "0",
    )


def enable_copr(spec):
    return run_helper("copr-enable", spec)


def remove_dnf_repo(repo_id):
    return run_helper("repo-remove", repo_id)


# Validation shared by the UI and the helper (the helper re-checks everything).
REPO_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:@+-]{0,99}$")
COPR_RE = re.compile(r"^@?[A-Za-z0-9_.-]+/[A-Za-z0-9_.+-]+$")
URL_RE = re.compile(r"^(https?|ftp|file)://[^\s'\"<>]+$")
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")


def is_valid_url(text):
    return bool(URL_RE.match(text.strip()))


# ---------------------------------------------------------------------------
# Flatpak remotes
# ---------------------------------------------------------------------------

@dataclass
class FlatpakRemote:
    """A Flatpak remote (repository)."""

    name: str
    title: str
    url: str
    installation: str
    enabled: bool
    options: list = field(default_factory=list)


def parse_flatpak_remotes(output):
    """Parse `flatpak remotes --show-disabled --columns=name,title,url,options`."""
    remotes = []
    for line in output.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 4 or parts[0] == "Name":
            continue
        name, title, url, options = parts[:4]
        opts = [o.strip() for o in options.split(",") if o.strip()]
        installation = "system"
        if "user" in opts:
            installation = "user"
        else:
            known = {"system", "disabled", "filtered", "no-enumerate", "no-deps", "oci"}
            custom = [o for o in opts if o not in known]
            if "system" not in opts and custom:
                installation = custom[0]
        remotes.append(
            FlatpakRemote(
                name=name,
                title=title or name,
                url=url,
                installation=installation,
                enabled="disabled" not in opts,
                options=opts,
            )
        )
    remotes.sort(key=lambda r: (r.title.lower(), r.installation))
    return remotes


def list_flatpak_remotes():
    code, out, err = run_command(
        ["flatpak", "remotes", "--show-disabled", "--columns=name,title,url,options"], timeout=60
    )
    if code != 0:
        raise BackendError(_("Could not list Flatpak remotes."), err)
    return parse_flatpak_remotes(out)


def _flatpak(args):
    code, out, err = run_command(["flatpak", *args], timeout=300)
    if code != 0:
        raise BackendError(_first_error_line(err, out) or _("The operation failed."), _join(out, err))
    return out


def add_flatpak_remote(name, url, installation):
    return _flatpak(["remote-add", "--if-not-exists", installation_flag(installation), name, url])


def remove_flatpak_remote(remote):
    return _flatpak(["remote-delete", installation_flag(remote.installation), remote.name])


def set_flatpak_remote_enabled(remote, enabled):
    return _flatpak([
        "remote-modify", "--enable" if enabled else "--disable",
        installation_flag(remote.installation), remote.name,
    ])
