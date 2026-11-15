#!/usr/bin/env python3
"""Drop an image file on the top-left half to rotate it 90 deg counter-clockwise,
top-right for 90 deg clockwise, bottom half for 180 deg. The dropped file itself
is rewritten in place, atomically.

Run: uv run spinner.py
Self-check (no display needed): uv run spinner.py --selftest
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import gi
from PIL import Image, ImageOps

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

SCRIPT = Path(__file__).resolve()

CSS = b"""
.dropzone { background-color: #fafafa; border: 3px dashed #b0b0b0; }
.dropzone.dragover { background-color: #e3f0ff; border-color: #3b82f6; }
.dropzone label { font-size: 72px; color: #444; }
.jobs { font-size: 15px; color: #777; }
"""


def rotate_file(path: Path, degrees: int) -> None:
    """Rotate the image file in place by degrees (positive = counter-clockwise).
    Raises on any failure; the original file is left untouched unless the
    replacement succeeds."""
    if path.is_symlink():
        path = path.resolve()
    if not path.is_file():
        raise ValueError("not a regular file")

    with Image.open(path) as im:
        if getattr(im, "is_animated", False):
            raise ValueError("animated images not supported")
        fmt = Image.registered_extensions().get(path.suffix.lower())
        if fmt not in Image.SAVE:
            raise ValueError(f"cannot save {path.suffix or 'extensionless file'}")
        # EXIF orientation first, otherwise viewers rotate it a second time.
        rotated = ImageOps.exif_transpose(im).rotate(degrees, expand=True)
        extra = {}
        if icc := im.info.get("icc_profile"):
            extra["icc_profile"] = icc
        if fmt in ("JPEG", "WEBP"):
            # shortcut: lossy re-encode. Upgrade to jpegtran for pixel-exact JPEG rotation.
            extra["quality"] = 95

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=path.suffix)
    os.close(fd)
    try:
        rotated.save(tmp, format=fmt, **extra)
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


class Jobs:
    """Count of running rotate workers, shown at the bottom of the window."""

    def __init__(self):
        self.count = 0
        self.label = Gtk.Label(label="0")
        self.label.get_style_context().add_class("jobs")
        self.label.set_width_chars(2)
        self.label.set_xalign(1)

    def start(self):
        self.count += 1
        self.label.set_text(str(self.count))

    def done(self):
        self.count -= 1
        self.label.set_text(str(self.count))


class DropZone(Gtk.EventBox):
    def __init__(self, icon: str, degrees: int, jobs: Jobs):
        super().__init__()
        self.degrees = degrees
        self.jobs = jobs
        self.get_style_context().add_class("dropzone")
        self.add(Gtk.Label(label=icon))
        self.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.drag_dest_add_uri_targets()
        self.connect("drag-motion", self.on_motion)
        self.connect("drag-leave", self.on_leave)
        self.connect("drag-data-received", self.on_drop)

    def on_motion(self, _w, ctx, _x, _y, time):
        Gdk.drag_status(ctx, Gdk.DragAction.COPY, time)
        self.get_style_context().add_class("dragover")
        return True

    def on_leave(self, _w, _ctx, _time):
        self.get_style_context().remove_class("dragover")

    def on_drop(self, _w, ctx, _x, _y, data, _info, time):
        self.get_style_context().remove_class("dragover")
        ctx.finish(True, False, time)
        for uri in data.get_uris():
            name = Gio.File.new_for_uri(uri).get_path()
            if not name:
                print(f"{uri}: not a local file", file=sys.stderr)
                continue
            proc = subprocess.Popen(
                [sys.executable, str(SCRIPT), "--rotate", str(self.degrees), name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.jobs.start()
            GLib.timeout_add(100, self.poll, proc)
        return True

    def poll(self, proc):
        if proc.poll() is None:
            return True
        self.jobs.done()
        err = proc.stderr.read().strip()
        if proc.returncode and err:
            print(err, file=sys.stderr)
        return False


def build_window() -> Gtk.Window:
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    jobs = Jobs()

    row = Gtk.Box(spacing=8)
    row.pack_start(DropZone("↺", 90, jobs), True, True, 0)
    row.pack_start(DropZone("↻", -90, jobs), True, True, 0)

    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    column.pack_start(row, True, True, 0)
    column.pack_start(DropZone("⇅", 180, jobs), True, True, 0)

    bar = Gtk.Box()
    bar.pack_end(jobs.label, False, False, 4)
    column.pack_start(bar, False, False, 0)

    win = Gtk.Window(title="Image Spinner")
    win.set_border_width(8)
    win.set_default_size(900, 450)
    win.add(column)
    return win


def selftest() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d, "t.png")
        im = Image.new("RGB", (2, 2), (0, 0, 0))
        im.putpixel((0, 0), (255, 0, 0))
        im.save(p)
        p.chmod(0o640)

        rotate_file(p, -90)
        with Image.open(p) as r:
            assert r.getpixel((1, 0)) == (255, 0, 0), "CW moves top-left to top-right"
        rotate_file(p, 90)
        with Image.open(p) as r:
            assert r.getpixel((0, 0)) == (255, 0, 0), "CW then CCW is identity"

        rotate_file(p, 90)
        with Image.open(p) as r:
            assert r.getpixel((0, 1)) == (255, 0, 0), "CCW moves top-left to bottom-left"

        rotate_file(p, -90)  # back to identity
        rotate_file(p, 180)
        with Image.open(p) as r:
            assert r.getpixel((1, 1)) == (255, 0, 0), "180 moves top-left to bottom-right"

        assert p.stat().st_mode & 0o777 == 0o640, "mode preserved"
        assert sorted(os.listdir(d)) == ["t.png"], "no temp files left"

        p.write_bytes(b"not an image")
        try:
            rotate_file(p, -90)
        except Exception:
            pass
        else:
            raise AssertionError("garbage file must fail")
        assert p.read_bytes() == b"not an image", "garbage file untouched"
    print("selftest ok")


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--rotate"]:
        path = Path(args[2])
        try:
            rotate_file(path, int(args[1]))
        except Exception as e:
            print(f"{path.name}: {e}", file=sys.stderr)
            sys.exit(1)
        return
    if "--selftest" in args:
        selftest()
        return
    win = build_window()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
