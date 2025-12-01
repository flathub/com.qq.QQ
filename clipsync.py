#!/usr/bin/env python3
"""Mirror X selections (e.g. inside Xvfb) into the Wayland clipboard."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Callable

DEFAULT_INTERVAL = float(os.environ.get("CLIPSYNC_INTERVAL", "0.3"))
DEFAULT_TEXT_MIME = "text/plain;charset=utf-8"
DEFAULT_BINARY_MIME = "application/octet-stream"
DEFAULT_SELECTIONS: Sequence[str] = ("clipboard", "primary")
IGNORED_TARGETS = {"targets", "timestamp", "multiple", "save_targets"}
FILE_TARGET_PREFS = [
    "application/x-gnome-copied-files",
    "x-special/nautilus-clipboard",
    "text/uri-list",
]
IMAGE_TARGET_PREFS = [
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/gif",
]
TEXT_TARGET_PREFS = [
    "text/plain;charset=utf-8",
    "text/plain",
    "text/plain; charset=utf-8",
    "text/plain;charset=UTF-8",
    "utf8_string",
    "string",
    "text",
    "text/html",
    "text/richtext",
]
TYPE_ALIASES = {
    "utf8_string": DEFAULT_TEXT_MIME,
    "text": DEFAULT_TEXT_MIME,
    "string": "text/plain",
    "text/plain; charset=utf-8": DEFAULT_TEXT_MIME,
    "text/plain;charset=utf-8": DEFAULT_TEXT_MIME,
    "text/plain;charset=utf-16": "text/plain",
    "text/plain;charset=utf16": "text/plain",
    "x-special/nautilus-clipboard": "application/x-gnome-copied-files",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Continuously copy the X clipboard and primary selection into the Wayland clipboard via wl-copy.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="Polling interval in seconds. Defaults to %(default)s.",
    )
    parser.add_argument(
        "--display",
        default=os.environ.get("DISPLAY"),
        help="X display/socket to talk to. Defaults to $DISPLAY.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Emit verbose debug logs.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to exec after the sync loop starts. Leave empty to keep syncing until interrupted.",
    )
    return parser


@dataclass
class ClipboardSelection:
    x_target: str
    wl_type: str
    category: str


def require_binary(name: str) -> None:
    if shutil.which(name) is None:
        print(f"error: {name} not found in PATH", file=sys.stderr)
        sys.exit(1)


def log(message: str, *, debug: bool = False, enabled: bool = False) -> None:
    if debug and not enabled:
        return
    prefix = "[debug] " if debug else ""
    print(prefix + message, file=sys.stderr)


def lower_map(targets: Iterable[str]) -> Dict[str, str]:
    return {t.lower(): t for t in targets}


def pick_preferred(
    targets: Sequence[str],
    preferences: Sequence[str],
    *,
    extra_predicate: Optional[Callable] = None,
) -> Optional[str]:
    mapping = lower_map(targets)
    for pref in preferences:
        key = pref.lower()
        if key in mapping:
            return mapping[key]
    if extra_predicate:
        for original in targets:
            if extra_predicate(original.lower()):
                return original
    return None


def classify_targets(targets: Sequence[str]) -> Optional[ClipboardSelection]:
    cleaned = [t for t in targets if t and t.lower() not in IGNORED_TARGETS]
    if not cleaned:
        return None

    def alias_for(target: str, *, default: str) -> str:
        return TYPE_ALIASES.get(target.lower(), target if target.startswith("text/") else default)

    image_target = pick_preferred(
        cleaned,
        IMAGE_TARGET_PREFS,
        extra_predicate=lambda t: t.startswith("image/")
    )
    if image_target:
        return ClipboardSelection(image_target, image_target, "image")
    
    file_target = pick_preferred(
        cleaned,
        FILE_TARGET_PREFS,
        extra_predicate=lambda t: "copied-files" in t or t.endswith("uri-list"),
    )
    if file_target:
        wl_type = TYPE_ALIASES.get(file_target.lower(), file_target)
        return ClipboardSelection(file_target, wl_type, "files")

    text_target = pick_preferred(
        cleaned,
        TEXT_TARGET_PREFS,
        extra_predicate=lambda t: t.startswith("text/") or t in {"utf8_string", "string"},
    )
    if text_target:
        wl_type = alias_for(text_target, default=DEFAULT_TEXT_MIME)
        return ClipboardSelection(text_target, wl_type, "text")

    fallback = cleaned[0]
    wl_type = TYPE_ALIASES.get(fallback.lower(), DEFAULT_BINARY_MIME)
    return ClipboardSelection(fallback, wl_type, "binary")


def run_xclip(
    selection: str,
    args: Sequence[str],
    *,
    env: Dict[str, str],
    text: bool = False,
) -> subprocess.CompletedProcess:
    base = ["xclip", "-selection", selection]
    return subprocess.run(
        base + list(args),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        text=text,
        timeout=5,
    )


def current_targets(selection: str, env: Dict[str, str], *, debug_enabled: bool) -> List[str]:
    try:
        proc = run_xclip(selection, ["-out", "-target", "TARGETS"], env=env, text=True)
    except subprocess.CalledProcessError as exc:
        log(
            f"failed to query TARGETS for {selection}: {exc.stderr.strip() or exc}",
            debug=True,
            enabled=debug_enabled,
        )
        return []
    targets = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return targets


def read_target(
    selection: str,
    target: str,
    env: Dict[str, str],
    *,
    debug_enabled: bool,
) -> Optional[bytes]:
    try:
        proc = run_xclip(selection, ["-out", "-target", target], env=env, text=False)
        return proc.stdout
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode().strip() if exc.stderr else str(exc)
        log(f"failed to read target {target} for {selection}: {err}", debug=True, enabled=debug_enabled)
        return None


def copy_to_wayland(data: bytes, wl_type: str) -> bool:
    try:
        subprocess.run(
            ["wl-copy", "--type", wl_type],
            input=data,
            check=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        log(f"wl-copy failed: {exc}", enabled=True)
        return False


def fingerprint(target: str, data: bytes) -> str:
    digest = hashlib.sha256(target.encode("utf-8") + b"\0" + data).hexdigest()
    return digest


def mirror_clipboards(
    selections: Sequence[str],
    env: Dict[str, str],
    interval: float,
    debug: bool,
    stop_event: threading.Event,
) -> None:
    last_marks: Dict[str, Optional[str]] = {sel: None for sel in selections}
    last_wayland_mark: Optional[str] = None

    readable = ", ".join(selections)
    log(
        f"Watching X selections [{readable}] on {env['DISPLAY']} every {interval:.2f}s",
        enabled=True,
    )

    while not stop_event.is_set():
        for sel in selections:
            if stop_event.is_set():
                break

            targets = current_targets(sel, env, debug_enabled=debug)
            selection_info = classify_targets(targets)
            if not selection_info:
                if last_marks.get(sel) is not None:
                    log(f"{sel} selection empty or unsupported; waiting", debug=True, enabled=debug)
                last_marks[sel] = None
                continue

            payload = read_target(sel, selection_info.x_target, env, debug_enabled=debug)
            if payload is None:
                continue

            mark = fingerprint(selection_info.wl_type, payload)
            if mark == last_marks.get(sel) or mark == last_wayland_mark:
                last_marks[sel] = mark
                continue

            if copy_to_wayland(payload, selection_info.wl_type):
                last_marks[sel] = mark
                last_wayland_mark = mark
                log(
                    f"Wayland clipboard updated from {sel} ({selection_info.category}) via '{selection_info.wl_type}' "
                    f"({len(payload)} bytes)",
                    enabled=True,
                )

        stop_event.wait(interval)


def run_wrapped_command(command: Sequence[str]) -> int:
    if not command:
        return 0
    proc = subprocess.Popen(command)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        return proc.wait()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    require_binary("xclip")
    require_binary("wl-copy")

    if not args.display:
        print("error: --display was not provided and $DISPLAY is unset", file=sys.stderr)
        return 1

    env = dict(os.environ)
    env["DISPLAY"] = args.display

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]

    stop_event = threading.Event()
    worker = threading.Thread(
        target=mirror_clipboards,
        args=(DEFAULT_SELECTIONS, env, args.interval, args.debug, stop_event),
        daemon=True,
    )
    worker.start()

    exit_code = 0
    try:
        if command:
            exit_code = run_wrapped_command(command)
        else:
            while not stop_event.wait(1):
                continue
    except KeyboardInterrupt:
        print("Interrupted, exiting...", file=sys.stderr)
        exit_code = 130
    finally:
        stop_event.set()
        worker.join(timeout=args.interval * 2)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
