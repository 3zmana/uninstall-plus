"""User settings, stored in ~/.config/uninstall-plus/settings.json."""

import json
import os

DEFAULTS = {
    "language": "system",  # "system", "en" or "fr"
    "theme": "system",  # "system", "light" or "dark"
}

ALLOWED = {
    "language": ("system", "en", "fr"),
    "theme": ("system", "light", "dark"),
}


def settings_path():
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config, "uninstall-plus", "settings.json")


class Settings:
    """Small JSON-backed settings store. Invalid values fall back to defaults."""

    def __init__(self, path=None):
        self.path = path or settings_path()
        self.values = dict(DEFAULTS)
        try:
            with open(self.path, encoding="utf-8") as handle:
                stored = json.load(handle)
            for key, value in stored.items():
                if key in ALLOWED and value in ALLOWED[key]:
                    self.values[key] = value
        except (OSError, ValueError, AttributeError):
            pass  # missing or broken file: keep the defaults

    def get(self, key):
        return self.values[key]

    def set(self, key, value):
        if key not in ALLOWED or value not in ALLOWED[key]:
            raise ValueError(f"Invalid setting {key}={value!r}")
        self.values[key] = value
        self.save()

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.values, handle, indent=2)
        os.replace(tmp, self.path)
