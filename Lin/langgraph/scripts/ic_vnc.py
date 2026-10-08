"""Minimal RFB client for the localhost-only IC VMware VNC endpoint."""

from __future__ import annotations

import argparse
import socket
import struct
import time
from pathlib import Path

XK_CTRL_L = 0xFFE3
XK_ALT_L = 0xFFE9
XK_SHIFT_L = 0xFFE1
XK_SUPER_L = 0xFFEB
XK_RETURN = 0xFF0D
XK_TAB = 0xFF09
XK_ESCAPE = 0xFF1B

SHIFT_CHARS = {
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "<": ",",
    ">": ".",
    "?": "/",
    "~": "`",
}


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("VNC connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class VncClient:
    def __init__(self, host: str, port: int, shared: bool = True) -> None:
        self.sock = socket.create_connection((host, port), timeout=10)
        self.sock.settimeout(10)
        version = _recv_exact(self.sock, 12)
        if not version.startswith(b"RFB "):
            raise RuntimeError(f"Unexpected RFB version: {version!r}")
        self.sock.sendall(b"RFB 003.008\n")
        count = _recv_exact(self.sock, 1)[0]
        if count == 0:
            raise RuntimeError(_recv_exact(self.sock, 4))
        security_types = set(_recv_exact(self.sock, count))
        if 1 not in security_types:
            raise RuntimeError(f"VNC requires unsupported authentication: {sorted(security_types)}")
        self.sock.sendall(b"\x01")
        result = struct.unpack(">I", _recv_exact(self.sock, 4))[0]
        if result != 0:
            raise RuntimeError(f"VNC security handshake failed: {result}")
        self.sock.sendall(b"\x01" if shared else b"\x00")
        server_init = _recv_exact(self.sock, 24)
        self.width, self.height = struct.unpack(">HH", server_init[:4])
        name_length = struct.unpack(">I", server_init[20:24])[0]
        self.name = _recv_exact(self.sock, name_length).decode("utf-8", "replace")
        self._set_pixel_format()
        self._set_encodings()

    def _set_pixel_format(self) -> None:
        pixel_format = struct.pack(
            ">BBBBHHHBBBxxx",
            32,
            24,
            0,
            1,
            255,
            255,
            255,
            16,
            8,
            0,
        )
        self.sock.sendall(b"\x00\x00\x00\x00" + pixel_format)

    def _set_encodings(self) -> None:
        self.sock.sendall(struct.pack(">BxH", 2, 1) + struct.pack(">i", 0))

    def key_event(self, keysym: int, down: bool) -> None:
        self.sock.sendall(struct.pack(">BBHI", 4, 1 if down else 0, 0, keysym))

    def pointer_event(self, x: int, y: int, button_mask: int) -> None:
        self.sock.sendall(struct.pack(">BBHH", 5, button_mask, x, y))

    def click(self, x: int, y: int) -> None:
        self.pointer_event(x, y, 1)
        self.pointer_event(x, y, 0)
        time.sleep(0.1)

    def scroll(self, x: int, y: int, steps: int) -> None:
        button = 8 if steps < 0 else 16
        for _ in range(abs(steps)):
            self.pointer_event(x, y, button)
            self.pointer_event(x, y, 0)
            time.sleep(0.08)

    def tap(self, keysym: int) -> None:
        self.key_event(keysym, True)
        self.key_event(keysym, False)
        time.sleep(0.03)

    def hotkey(self, *keysyms: int) -> None:
        for keysym in keysyms:
            self.key_event(keysym, True)
        for keysym in reversed(keysyms):
            self.key_event(keysym, False)
        time.sleep(0.08)

    def type_text(self, text: str) -> None:
        for char in text:
            if char == "\n" or char == "\r":
                self.tap(XK_RETURN)
                continue
            if char == "\t":
                self.tap(XK_TAB)
                continue
            shift = char in SHIFT_CHARS or char.isupper()
            base = SHIFT_CHARS.get(char, char.lower() if char.isupper() else char)
            if shift:
                self.key_event(XK_SHIFT_L, True)
            self.tap(ord(base))
            if shift:
                self.key_event(XK_SHIFT_L, False)

    def framebuffer(self) -> Image.Image:
        # Keyboard-only control does not need Pillow.  Import it lazily so the
        # diagnostic/automation path works in the lean EDA Python environment.
        from PIL import Image

        self.sock.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, 0, 0))
        while True:
            message_type = _recv_exact(self.sock, 1)[0]
            if message_type == 0:
                break
            if message_type == 1:
                _recv_exact(self.sock, 3)
                first_color = struct.unpack(">H", _recv_exact(self.sock, 2))[0]
                color_count = struct.unpack(">H", _recv_exact(self.sock, 2))[0]
                _recv_exact(self.sock, color_count * 6)
                del first_color
                continue
            if message_type == 2:
                _recv_exact(self.sock, 1)
                continue
            if message_type == 3:
                _recv_exact(self.sock, 3)
                length = struct.unpack(">I", _recv_exact(self.sock, 4))[0]
                _recv_exact(self.sock, length)
                continue
            raise RuntimeError(f"Unsupported VNC server message type: {message_type}")

        _recv_exact(self.sock, 1)
        rect_count = struct.unpack(">H", _recv_exact(self.sock, 2))[0]
        image = Image.new("RGB", (self.width, self.height), "black")
        for _ in range(rect_count):
            x, y, width, height, encoding = struct.unpack(">HHHHi", _recv_exact(self.sock, 12))
            if encoding == -223:
                self.width = width
                self.height = height
                image = Image.new("RGB", (self.width, self.height), "black")
                continue
            if encoding != 0:
                raise RuntimeError(f"Unsupported framebuffer encoding: {encoding}")
            raw = _recv_exact(self.sock, width * height * 4)
            pixels = bytearray(width * height * 3)
            for index in range(width * height):
                offset = index * 4
                out = index * 3
                pixels[out] = raw[offset]
                pixels[out + 1] = raw[offset + 1]
                pixels[out + 2] = raw[offset + 2]
            rect = Image.frombytes("RGB", (width, height), bytes(pixels))
            image.paste(rect, (x, y))
        return image

    def close(self) -> None:
        self.sock.close()


def parse_combo(value: str) -> list[int]:
    aliases = {
        "ctrl": XK_CTRL_L,
        "alt": XK_ALT_L,
        "shift": XK_SHIFT_L,
        "super": XK_SUPER_L,
        "escape": XK_ESCAPE,
    }
    keys = []
    for part in value.split("+"):
        key = part.strip().lower()
        if key in aliases:
            keys.append(aliases[key])
        elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
            keys.append(0xFFBE + int(key[1:]) - 1)
        elif len(key) == 1:
            keys.append(ord(key))
        else:
            raise ValueError(f"Unsupported key in combo: {part}")
    return keys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5901)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--hotkey")
    parser.add_argument("--type")
    parser.add_argument("--click")
    parser.add_argument("--scroll")
    parser.add_argument("--exclusive", action="store_true")
    parser.add_argument("--delay", type=float, default=0.0)
    args = parser.parse_args()

    client = VncClient(args.host, args.port, shared=not args.exclusive)
    try:
        if args.delay:
            time.sleep(args.delay)
        if args.hotkey:
            client.hotkey(*parse_combo(args.hotkey))
        if args.click:
            x_text, y_text = args.click.split(",", 1)
            client.click(int(x_text), int(y_text))
        if args.scroll:
            x_text, y_text, steps_text = args.scroll.split(",", 2)
            client.scroll(int(x_text), int(y_text), int(steps_text))
        if args.type:
            client.type_text(args.type)
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            client.framebuffer().save(args.screenshot)
        time.sleep(0.3)
    finally:
        client.close()


if __name__ == "__main__":
    main()
