#!/usr/bin/env python3
"""Rebuild committed native/web icons from packaging/icon.svg.

Development-only command: python scripts/generate_icons.py [--check].
Requires Pillow; neither application startup nor packaging runs this generator.
The SVG uses paths, so output is independent of installed fonts.
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path("backend/inkreference/assets")
WEB = Path("frontend/public")
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ICNS_SIZES = ((b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128),
              (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024))


def _contours(path: str) -> list[list[tuple[float, float]]]:
    """Flatten the canonical M/L/C/Z SVG path, preserving its counter."""
    tokens = re.findall(r"[MLCZ]|-?(?:\d+(?:\.\d*)?|\.\d+)", path)
    contours: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    position = (0.0, 0.0)
    index = 0
    while index < len(tokens):
        command = tokens[index]
        index += 1
        if command == "Z":
            contours.append(current)
            current = []
            continue
        count = 6 if command == "C" else 2
        points = list(map(float, tokens[index:index + count]))
        index += count
        if command in {"M", "L"}:
            position = (points[0], points[1])
            current.append(position)
        elif command == "C":
            start = position
            for step in range(1, 65):
                t = step / 64
                inverse = 1 - t
                current.append(tuple(
                    inverse ** 3 * start[axis] + 3 * inverse ** 2 * t * points[axis]
                    + 3 * inverse * t ** 2 * points[axis + 2] + t ** 3 * points[axis + 4]
                    for axis in (0, 1)
                ))
            position = (points[4], points[5])
        else:
            raise ValueError(f"Unsupported icon path command: {command}")
    if current or len(contours) != 2:
        raise ValueError("The icon needs a closed R outline and counter")
    return contours


def render(svg: bytes, size: int) -> Image.Image:
    document = ET.fromstring(svg)
    rectangle = document.find("{http://www.w3.org/2000/svg}rect")
    path = document.find("{http://www.w3.org/2000/svg}path")
    if rectangle is None or path is None:
        raise ValueError("Icon SVG must have its background and R path")
    scale = size * 4 / 64
    canvas = Image.new("RGBA", (size * 4, size * 4))
    draw = ImageDraw.Draw(canvas)
    background = rectangle.attrib["fill"]
    draw.rounded_rectangle((0, 0, size * 4 - 1, size * 4 - 1),
                           radius=float(rectangle.attrib["rx"]) * scale, fill=background)
    for index, contour in enumerate(_contours(path.attrib["d"])):
        draw.polygon([(x * scale, y * scale) for x, y in contour],
                     fill=path.attrib["fill"] if index == 0 else background)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, "PNG", compress_level=9)
    return output.getvalue()


def generated_files(root: Path = ROOT) -> dict[Path, bytes]:
    svg = (root / "packaging/icon.svg").read_bytes()
    sizes = set(ICO_SIZES) | {size for _, size in ICNS_SIZES} | {180, 192}
    images = {size: _png(render(svg, size)) for size in sorted(sizes)}
    ico = bytearray(struct.pack("<HHH", 0, 1, len(ICO_SIZES)))
    offset = 6 + 16 * len(ICO_SIZES)
    for size in ICO_SIZES:
        content = images[size]
        ico.extend(struct.pack("<BBBBHHII", size if size < 256 else 0,
                               size if size < 256 else 0, 0, 0, 1, 32, len(content), offset))
        offset += len(content)
    for size in ICO_SIZES:
        ico.extend(images[size])
    chunks = b"".join(kind + struct.pack(">I", len(images[size]) + 8) + images[size]
                      for kind, size in ICNS_SIZES)
    return {
        NATIVE / "icon.svg": svg,
        NATIVE / "icon.png": images[512],
        NATIVE / "icon.ico": bytes(ico),
        NATIVE / "icon.icns": b"icns" + struct.pack(">I", len(chunks) + 8) + chunks,
        WEB / "app-icon.svg": svg,
        WEB / "app-icon.png": images[192],
        WEB / "apple-touch-icon.png": images[180],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for relative, content in generated_files().items():
        path = ROOT / relative
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                stale.append(str(relative))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if stale:
        parser.exit(1, "Rebuild icon assets: " + ", ".join(stale) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
