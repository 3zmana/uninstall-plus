"""Small shared UI helpers."""

import threading

from gi.repository import Adw, GLib, Gtk, Pango

from .i18n import _

CSS = """
.tag {
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 0.8em;
  font-weight: 600;
  background-color: alpha(currentColor, 0.10);
}
.tag.copr { background-color: alpha(#e66100, 0.18); }
.tag.system { background-color: alpha(#3584e4, 0.16); }
.tag.user { background-color: alpha(#2ec27e, 0.18); }
.package-row { padding: 2px 6px; }
.package-name { font-weight: 700; }
.mono-output {
  font-family: monospace;
  font-size: 0.85em;
  padding: 10px;
  border-radius: 8px;
}
.filter-bar { padding: 6px 12px; }
.counter { font-size: 0.85em; }
"""


def load_css():
    provider = Gtk.CssProvider()
    try:
        provider.load_from_string(CSS)  # GTK >= 4.12
    except AttributeError:
        provider.load_from_data(CSS.encode())
    from gi.repository import Gdk

    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


# Width of the central column shared by the filter bars and the lists.
CONTENT_WIDTH = 960
CONTENT_THRESHOLD = 720


def centered(child):
    """Keep `child` within the central column, like the list below it."""
    return Adw.Clamp(maximum_size=CONTENT_WIDTH, tightening_threshold=CONTENT_THRESHOLD,
                     child=child)


def run_in_thread(func, on_done, *args):
    """Run func(*args) in a worker thread, then call on_done(result, error)
    on the main loop. Exactly one of result/error is meaningful."""

    def deliver(result, error):
        on_done(result, error)
        return GLib.SOURCE_REMOVE

    def worker():
        try:
            result, error = func(*args), None
        except Exception as exc:  # every failure is reported to the UI
            result, error = None, exc
        GLib.idle_add(deliver, result, error)

    threading.Thread(target=worker, daemon=True).start()


def make_spinner(size=32):
    """Adw.Spinner on libadwaita >= 1.6, Gtk.Spinner otherwise."""
    if hasattr(Adw, "Spinner"):
        spinner = Adw.Spinner()
        spinner.set_size_request(size, size)
        return spinner
    spinner = Gtk.Spinner(spinning=True)
    spinner.set_size_request(size, size)
    return spinner


def make_wrap_box():
    """A container whose children wrap to a new line on narrow windows.

    Uses Adw.WrapBox (libadwaita >= 1.7, GNOME 48 and later) and falls back
    to a Gtk.FlowBox on older versions.
    """
    if hasattr(Adw, "WrapBox"):
        return Adw.WrapBox(child_spacing=12, line_spacing=8)
    box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12,
                      row_spacing=8, max_children_per_line=8, homogeneous=False)
    return box


def ellipsizing_dropdown(strings, max_chars=22):
    """A Gtk.DropDown whose button shortens long entries."""
    dropdown = Gtk.DropDown.new_from_strings(strings)

    def make_factory(max_width):
        factory = Gtk.SignalListItemFactory()

        def setup(_f, item):
            label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
            if max_width:
                label.set_max_width_chars(max_width)
            item.set_child(label)

        def bind(_f, item):
            item.get_child().set_text(item.get_item().get_string())

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    dropdown.set_factory(make_factory(max_chars))
    dropdown.set_list_factory(make_factory(0))
    return dropdown


def tag(text, *classes, tooltip=None):
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER, ellipsize=Pango.EllipsizeMode.END, max_width_chars=28)
    label.add_css_class("tag")
    for css_class in classes:
        label.add_css_class(css_class)
    if tooltip:
        label.set_tooltip_text(tooltip)
    return label


def dim(widget):
    widget.add_css_class("dim-label")
    widget.add_css_class("dimmed")
    return widget


def output_view(text, min_height=160, wrap=False):
    """Read-only monospace text box used to show command output.

    Tables (like DNF transactions) are not wrapped and scroll sideways instead.
    """
    view = Gtk.TextView(editable=False, cursor_visible=False,
                        wrap_mode=Gtk.WrapMode.WORD_CHAR if wrap else Gtk.WrapMode.NONE)
    view.add_css_class("mono-output")
    view.add_css_class("card")
    view.get_buffer().set_text(text or "")
    scrolled = Gtk.ScrolledWindow(
        child=view,
        min_content_height=min_height,
        max_content_height=360,
        propagate_natural_height=True,
        hscrollbar_policy=Gtk.PolicyType.NEVER if wrap else Gtk.PolicyType.AUTOMATIC,
    )
    return scrolled


def show_error(parent, heading, error=None, details=""):
    """Show an error dialog. `error` may be a BackendError with details."""
    body = str(error) if error else ""
    details = details or getattr(error, "details", "")
    dialog = Adw.AlertDialog(heading=heading, body=body)
    if details:
        expander = Gtk.Expander(label=_("Details"))
        expander.set_child(output_view(details, 120, wrap=True))
        dialog.set_extra_child(expander)
    dialog.add_response("close", _("Close"))
    dialog.set_default_response("close")
    dialog.present(parent)


def status_page(icon, title, description=""):
    return Adw.StatusPage(icon_name=icon, title=title, description=description, vexpand=True)


def loading_page(text):
    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=18,
        valign=Gtk.Align.CENTER,
        halign=Gtk.Align.CENTER,
        vexpand=True,
    )
    box.append(make_spinner(48))
    box.append(dim(Gtk.Label(label=text)))
    return box


def entry_list(*rows):
    """A boxed list holding preference rows, for use inside dialogs."""
    listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    listbox.add_css_class("boxed-list")
    for row in rows:
        listbox.append(row)
    return listbox
