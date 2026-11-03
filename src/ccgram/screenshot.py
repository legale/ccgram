"""Terminal text → PNG screenshot renderer.

Converts captured tmux pane text (with optional ANSI color codes) into a
dark-background PNG image using JetBrains Mono font. Supports full ANSI
color parsing (16/256/RGB).

Key function: text_to_image(text, font_size, with_ansi) → PNG bytes.
"""

import asyncio
import io
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import structlog

_RE_ANSI_SGR = re.compile(r"\x1b\[([0-9;]*)m")

logger = structlog.get_logger()

_FONT_CANDIDATES: list[Path] = [
    Path(__file__).parent / "fonts" / "JetBrainsMono-Regular.ttf",
    Path("/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
]

# ANSI color mapping (basic 16 colors)
_ANSI_COLORS: dict[int, tuple[int, int, int]] = {
    # Standard colors (30-37, 40-47)
    0: (0, 0, 0),  # Black
    1: (205, 49, 49),  # Red
    2: (13, 188, 121),  # Green
    3: (229, 229, 16),  # Yellow
    4: (36, 114, 200),  # Blue
    5: (188, 63, 188),  # Magenta
    6: (17, 168, 205),  # Cyan
    7: (229, 229, 229),  # White
    # Bright colors (90-97, 100-107)
    8: (102, 102, 102),  # Bright Black
    9: (241, 76, 76),  # Bright Red
    10: (35, 209, 139),  # Bright Green
    11: (245, 245, 67),  # Bright Yellow
    12: (59, 142, 234),  # Bright Blue
    13: (214, 112, 214),  # Bright Magenta
    14: (41, 184, 219),  # Bright Cyan
    15: (255, 255, 255),  # Bright White
}

# Default colors for terminals
_DEFAULT_FG = (212, 212, 212)  # Light gray
_DEFAULT_BG = (30, 30, 30)  # Dark gray


@dataclass
class TextStyle:
    """Text styling information from ANSI codes."""

    fg_color: tuple[int, int, int] = _DEFAULT_FG
    bg_color: tuple[int, int, int] | None = None


@dataclass
class StyledSegment:
    """A text segment with its styling."""

    text: str
    style: TextStyle


_font_cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load JetBrains Mono font, falling back to system mono or Pillow default."""
    if size not in _font_cache:
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont | None = None
        for path in _FONT_CANDIDATES:
            if path.exists():
                try:
                    font = ImageFont.truetype(str(path), size)
                    break
                except OSError:
                    continue
        if font is None:
            logger.warning("JetBrains Mono font not found, using Pillow default")
            font = ImageFont.load_default(size=size)
        _font_cache[size] = font
    return _font_cache[size]


def _parse_ansi_line(line: str) -> list[StyledSegment]:
    """Parse a line with ANSI escape codes into styled segments."""
    segments: list[StyledSegment] = []
    current_style = TextStyle()
    pos = 0

    for match in _RE_ANSI_SGR.finditer(line):
        text_before = line[pos : match.start()]
        if text_before:
            segments.append(StyledSegment(text_before, current_style))

        codes = match.group(1)
        if codes:
            current_style = _apply_ansi_codes(current_style, codes)
        else:
            current_style = TextStyle()

        pos = match.end()

    text_after = line[pos:]
    if text_after:
        segments.append(StyledSegment(text_after, current_style))

    return segments if segments else [StyledSegment("", TextStyle())]


def _apply_ansi_codes(style: TextStyle, codes: str) -> TextStyle:
    """Apply ANSI color codes to a text style."""
    new_style = TextStyle(
        fg_color=style.fg_color,
        bg_color=style.bg_color,
    )

    parts = [int(c) for c in codes.split(";") if c]
    i = 0
    while i < len(parts):
        code = parts[i]

        if code == 0:  # Reset
            new_style = TextStyle()
        elif 30 <= code <= 37:  # Foreground color
            new_style.fg_color = _ANSI_COLORS[code - 30]
        elif code == 38:  # Extended foreground color
            if i + 1 < len(parts) and parts[i + 1] == 5:  # 256 color
                if i + 2 < len(parts):
                    color_idx = parts[i + 2] % 256
                    if color_idx < 16:
                        new_style.fg_color = _ANSI_COLORS[color_idx]
                    else:
                        new_style.fg_color = _approximate_256_color(color_idx)
                    i += 2
            elif (
                i + 1 < len(parts) and parts[i + 1] == 2 and i + 4 < len(parts)
            ):  # RGB color
                new_style.fg_color = (parts[i + 2], parts[i + 3], parts[i + 4])
                i += 4
        elif code == 39:  # Default foreground
            new_style.fg_color = _DEFAULT_FG
        elif 40 <= code <= 47:  # Background color
            new_style.bg_color = _ANSI_COLORS[code - 40]
        elif code == 48:  # Extended background color
            if i + 1 < len(parts) and parts[i + 1] == 5:  # 256 color
                if i + 2 < len(parts):
                    color_idx = parts[i + 2] % 256
                    if color_idx < 16:
                        new_style.bg_color = _ANSI_COLORS[color_idx]
                    else:
                        new_style.bg_color = _approximate_256_color(color_idx)
                    i += 2
            elif (
                i + 1 < len(parts) and parts[i + 1] == 2 and i + 4 < len(parts)
            ):  # RGB color
                new_style.bg_color = (parts[i + 2], parts[i + 3], parts[i + 4])
                i += 4
        elif code == 49:  # Default background
            new_style.bg_color = None
        elif 90 <= code <= 97:  # Bright foreground color
            new_style.fg_color = _ANSI_COLORS[code - 90 + 8]
        elif 100 <= code <= 107:  # Bright background color
            new_style.bg_color = _ANSI_COLORS[code - 100 + 8]

        i += 1

    return new_style


def _approximate_256_color(idx: int) -> tuple[int, int, int]:
    """Approximate a 256-color palette index to RGB."""
    if idx < 16:
        return _ANSI_COLORS[idx]
    if idx < 232:
        idx -= 16
        r = (idx // 36) * 51
        g = ((idx % 36) // 6) * 51
        b = (idx % 6) * 51
        return (r, g, b)
    gray = 8 + (idx - 232) * 10
    return (gray, gray, gray)


async def text_to_image(
    text: str,
    font_size: int | None = None,
    with_ansi: bool = True,
    *,
    live_mode: bool = False,
    palette_colors: int | None = None,
) -> bytes:
    """Render monospace text onto a dark-background image and return PNG bytes."""
    # Lazy: config resolved at call time for testability
    from .config import config

    base_font_size = font_size if font_size is not None else config.screenshot_font_size
    effective_font_size = (
        max(10, int(base_font_size * 0.75)) if live_mode else base_font_size
    )

    def _render_image() -> bytes:
        font = _load_font(effective_font_size)

        lines = text.split("\n")
        padding = 16

        if with_ansi:
            line_segments = [_parse_ansi_line(line) for line in lines]
        else:
            line_segments = [[StyledSegment(line, TextStyle())] for line in lines]

        # Measure text size
        dummy = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(dummy)
        line_height = int(effective_font_size * 1.4)
        max_width = 0
        for segments in line_segments:
            w = 0
            for seg in segments:
                bbox = draw.textbbox((0, 0), seg.text, font=font)
                w += bbox[2] - bbox[0]
            max_width = max(max_width, w)

        # Ensure minimum dimensions to avoid degenerate images
        img_width = max(int(max_width) + padding * 2, 200)
        img_height = max(
            line_height * len(lines) + padding * 2, line_height + padding * 2
        )

        img = Image.new("RGB", (img_width, img_height), _DEFAULT_BG)
        draw = ImageDraw.Draw(img)

        y = padding
        for segments in line_segments:
            x = padding
            for seg in segments:
                if seg.style.bg_color:
                    bbox = draw.textbbox((x, y), seg.text, font=font)
                    draw.rectangle(
                        [bbox[0], y, bbox[2], y + line_height], fill=seg.style.bg_color
                    )

                draw.text((x, y), seg.text, fill=seg.style.fg_color, font=font)
                bbox = draw.textbbox((0, 0), seg.text, font=font)
                x += bbox[2] - bbox[0]
            y += line_height

        buf = io.BytesIO()
        if live_mode:
            img = img.quantize(colors=32)
            img.save(buf, format="PNG", optimize=True, compress_level=9)
        else:
            colors = (
                palette_colors
                if palette_colors is not None
                else config.screenshot_palette_colors
            )
            if colors > 0:
                img = img.quantize(colors=colors)
                img.save(buf, format="PNG", optimize=True, compress_level=6)
            else:
                img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    return await asyncio.to_thread(_render_image)
