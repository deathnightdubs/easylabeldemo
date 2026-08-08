#!/usr/bin/env python3
"""
EasyLabel coin-holder label generator.

Features:
- Excel-driven front/back labels
- Full per-corner column configurability
- Separate Latin and CJK fonts
- Rectangular holder dimensions
- Full-circle, point, tick, or no cutout guides
- Optional centre X or filled point
- Independent per-coin front/back X-Y cutout offsets
- CLI-only operation

Dependencies:
    pip install reportlab openpyxl pillow
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Optional, Tuple
import os
import re
import sys

from openpyxl import load_workbook
from PIL import Image
from reportlab.lib import pagesizes
from reportlab.lib.pagesizes import landscape, portrait
from reportlab.lib.units import cm, inch, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

try:
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    CID_AVAILABLE = True
except ImportError:
    CID_AVAILABLE = False


# -----------------------------------------------------------------------------
# Constants and data models
# -----------------------------------------------------------------------------

MEASUREMENT_PATTERN = re.compile(
    r"^([+-]?(?:\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+))\s*(in|mm|cm|pt)?$",
    re.IGNORECASE,
)
COLUMN_PATTERN = re.compile(r"^[A-Z]+$")
CJK_PATTERN = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf"
    r"\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]"
)

DEFAULT_COLUMN_MAP = {
    "diameter": "A",
    "country": "B",
    "ftl": ["C", "D", "E"],
    "ftr": ["F", "G", "H"],
    "fbl": ["I", "J", "K"],
    "fbr": ["L", "M", "N"],
    "rtl": ["O", "P", "Q"],
    "rtr": ["R", "S", "T"],
    "rbl": ["U", "V", "W"],
    "rbr": ["X", "Y", "Z"],
}


@dataclass(frozen=True)
class Offset:
    """A drawing offset stored internally in ReportLab points."""

    x: float = 0.0
    y: float = 0.0


@dataclass
class Coin:
    diameter_mm: float
    country: str
    front: Dict[str, List[str]]
    back: Dict[str, List[str]]
    front_offset: Offset
    back_offset: Offset


# -----------------------------------------------------------------------------
# General utilities
# -----------------------------------------------------------------------------

def script_directory() -> str:
    """Return the folder beside the script, or beside the packaged EXE."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resolve_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    cleaned = str(path).strip()
    if os.path.isabs(cleaned):
        return cleaned
    return os.path.join(script_directory(), cleaned)


def parse_number(number_text: str) -> float:
    """Parse a decimal, fraction, or signed mixed number."""
    text = number_text.strip()

    if " " in text:
        sign = -1.0 if text.startswith("-") else 1.0
        unsigned = text.lstrip("+-")
        whole_text, fraction_text = unsigned.split()
        return sign * (float(whole_text) + float(Fraction(fraction_text)))

    if "/" in text:
        return float(Fraction(text))

    return float(text)


def parse_measurement(value, default_unit: str = "pt") -> float:
    """
    Convert a measurement to ReportLab points.

    Examples: 0.5in, 10mm, -1/16in, 6pt.
    A unitless value uses ``default_unit``.
    """
    if isinstance(value, (int, float)):
        number = float(value)
        unit = default_unit
    else:
        text = str(value).strip().lower()
        match = MEASUREMENT_PATTERN.fullmatch(text)
        if not match:
            raise ValueError(f"Invalid measurement: {value!r}")
        number_text, specified_unit = match.groups()
        number = parse_number(number_text)
        unit = specified_unit or default_unit

    unit_scales = {
        "pt": 1.0,
        "in": inch,
        "mm": mm,
        "cm": cm,
    }
    try:
        return number * unit_scales[unit.lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported unit: {unit!r}") from exc


def parse_offset(value, fallback: float = 0.0) -> float:
    """
    Parse an offset value into points.

    Plain Excel numbers and unitless strings are interpreted as millimetres.
    Blank cells use the supplied fallback.
    """
    if value is None or str(value).strip() == "":
        return fallback
    return parse_measurement(value, default_unit="mm")


def parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1", "on"}:
        return True
    if text in {"false", "no", "n", "0", "off"}:
        return False
    return default


def load_config(path: str) -> Dict[str, str]:
    """
    Load ``key = value`` settings from a text file.

    Full-line comments begin with ``#``. Inline comments are also supported
    when the ``#`` is preceded by whitespace, for example::

        v_spacing = 0.3in  # gap between holders

    A value beginning with ``#`` is preserved so hexadecimal colours such as
    ``line_color = #333333`` continue to work.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    config: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as config_file:
        for raw_line in config_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = (part.strip() for part in line.split("=", 1))

            # Remove comments such as ``0.3in  # explanation`` while keeping
            # values that begin with #, especially hexadecimal colours.
            value = re.sub(r"\s+#.*$", "", value).strip()

            if not key:
                continue
            config[key] = value

    return config


def normalize_column(column: str, setting_name: str) -> str:
    cleaned = str(column).strip().upper()
    if not COLUMN_PATTERN.fullmatch(cleaned):
        raise ValueError(
            f"Invalid Excel column {column!r} for '{setting_name}'. "
            "Use letters such as A, Z, AA, or BC."
        )
    return cleaned


def optional_column(config: Dict[str, str], setting_name: str) -> Optional[str]:
    """Return an optional configured Excel column, or None when disabled."""
    value = config.get(setting_name, "").strip()
    if value.lower() in {"", "none", "off", "false", "disabled"}:
        return None
    return normalize_column(value, setting_name)


def get_group_columns(
    config: Dict[str, str], group_name: str, defaults: List[str]
) -> List[str]:
    """
    Read a corner's configurable Excel columns.

    Supported forms:
        ftl = C,D,E
    or:
        ftl_1 = C
        ftl_2 = D
        ftl_3 = E
    """
    combined = config.get(group_name, "").strip()
    if combined:
        return [
            normalize_column(part, group_name)
            for part in combined.split(",")
            if part.strip()
        ]

    numbered: List[str] = []
    for index in range(1, 33):
        key = f"{group_name}_{index}"
        value = config.get(key, "").strip()
        if value:
            numbered.append(normalize_column(value, key))

    if numbered:
        return numbered

    return list(defaults)


def parse_rgb(value: str) -> Tuple[float, float, float]:
    """Parse a small set of names or #RGB/#RRGGBB into 0-1 RGB values."""
    named = {
        "black": (0.0, 0.0, 0.0),
        "white": (1.0, 1.0, 1.0),
        "red": (1.0, 0.0, 0.0),
        "green": (0.0, 0.5, 0.0),
        "blue": (0.0, 0.0, 1.0),
        "gray": (0.5, 0.5, 0.5),
        "grey": (0.5, 0.5, 0.5),
    }

    text = str(value).strip().lower()
    if text in named:
        return named[text]

    text = text.removeprefix("#")
    if len(text) == 3:
        text = "".join(character * 2 for character in text)

    if len(text) != 6 or not re.fullmatch(r"[0-9a-f]{6}", text):
        raise ValueError(
            f"Invalid line_color {value!r}. Use a name such as black or a hex "
            "value such as #808080."
        )

    return tuple(int(text[index:index + 2], 16) / 255.0 for index in (0, 2, 4))


# -----------------------------------------------------------------------------
# Font handling
# -----------------------------------------------------------------------------

def register_font(font_name: str, ttf_path: Optional[str] = None) -> str:
    """Register an optional TTF file or use a built-in ReportLab font."""
    if ttf_path:
        resolved = resolve_path(ttf_path)
        if not resolved or not os.path.isfile(resolved):
            print(f"WARNING: Font file not found: {resolved}")
        else:
            registered_name = f"UserFont_{abs(hash(os.path.abspath(resolved)))}"
            try:
                pdfmetrics.registerFont(TTFont(registered_name, resolved))
                print(f"Registered font: {resolved}")
                return registered_name
            except Exception as exc:
                print(f"WARNING: Could not register font '{resolved}': {exc}")

    builtin_fonts = {
        "Helvetica",
        "Helvetica-Bold",
        "Helvetica-Oblique",
        "Helvetica-BoldOblique",
        "Courier",
        "Courier-Bold",
        "Courier-Oblique",
        "Courier-BoldOblique",
        "Times-Roman",
        "Times-Bold",
        "Times-Italic",
        "Times-BoldItalic",
    }

    if font_name in builtin_fonts:
        return font_name

    print(f"WARNING: Unknown built-in font '{font_name}'; using Helvetica.")
    return "Helvetica"


def contains_cjk(text: str) -> bool:
    return bool(CJK_PATTERN.search(text))


def coins_contain_cjk(coins: List[Coin]) -> bool:
    for coin in coins:
        for side in (coin.front, coin.back):
            for lines in side.values():
                if any(contains_cjk(line) for line in lines):
                    return True
    return False


def select_cjk_font(
    coins: List[Coin], config: Dict[str, str], primary_font: str
) -> Optional[str]:
    """Register the configured CJK font only when CJK text is present."""
    if not coins_contain_cjk(coins):
        return None

    configured_path = config.get("cjk_font_ttf_path", "").strip()
    if configured_path:
        return register_font("Helvetica", configured_path)

    if CID_AVAILABLE:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            print("Detected CJK text; using ReportLab's STSong-Light fallback.")
            return "STSong-Light"
        except Exception as exc:
            print(f"WARNING: Could not register STSong-Light: {exc}")

    print(
        "WARNING: CJK text was detected but no working CJK font was configured. "
        "Set cjk_font_ttf_path in config.txt."
    )
    return primary_font


# -----------------------------------------------------------------------------
# Excel input
# -----------------------------------------------------------------------------

def read_cell_group(worksheet, columns: List[str], row: int) -> List[str]:
    values: List[str] = []
    for column in columns:
        value = worksheet[f"{column}{row}"].value
        if value is not None:
            values.append(str(value))
    return values


def read_offset_cell(
    worksheet,
    column: Optional[str],
    row: int,
    fallback: float,
    label: str,
) -> float:
    if column is None:
        return fallback

    cell_reference = f"{column}{row}"
    value = worksheet[cell_reference].value
    try:
        return parse_offset(value, fallback)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid {label} in Excel cell {cell_reference}: {value!r}. "
            "Use a number in millimetres or a value such as -0.5mm or 0.02in."
        ) from exc


def read_coins_from_excel(path: str, config: Dict[str, str]) -> List[Coin]:
    workbook = load_workbook(path, data_only=True)
    worksheet = workbook.worksheets[0]

    start_row = int(config.get("start_row", 3))
    end_row = int(config.get("end_row", 100))

    diameter_column = normalize_column(
        config.get(
            "diameter_column",
            config.get("DiameterColumn", DEFAULT_COLUMN_MAP["diameter"]),
        ),
        "diameter_column",
    )
    country_column = normalize_column(
        config.get(
            "country_column",
            config.get("CountryColumn", DEFAULT_COLUMN_MAP["country"]),
        ),
        "country_column",
    )

    groups = {
        name: get_group_columns(config, name, DEFAULT_COLUMN_MAP[name])
        for name in ("ftl", "ftr", "fbl", "fbr", "rtl", "rtr", "rbl", "rbr")
    }

    # Optional per-row columns. If omitted, only the config defaults are used.
    offset_columns = {
        "front_x": optional_column(config, "front_offset_x_column"),
        "front_y": optional_column(config, "front_offset_y_column"),
        "back_x": optional_column(config, "back_offset_x_column"),
        "back_y": optional_column(config, "back_offset_y_column"),
    }

    # Config defaults accept units. Unitless defaults are interpreted as mm.
    default_front = Offset(
        x=parse_offset(config.get("default_front_offset_x", "0mm")),
        y=parse_offset(config.get("default_front_offset_y", "0mm")),
    )
    default_back = Offset(
        x=parse_offset(config.get("default_back_offset_x", "0mm")),
        y=parse_offset(config.get("default_back_offset_y", "0mm")),
    )

    coins: List[Coin] = []

    for row in range(start_row, end_row + 1):
        diameter_value = worksheet[f"{diameter_column}{row}"].value
        if diameter_value is None:
            continue

        try:
            diameter_mm = float(diameter_value)
        except (TypeError, ValueError):
            print(
                f"WARNING: Skipping row {row}; diameter cell "
                f"{diameter_column}{row} is not numeric: {diameter_value!r}"
            )
            continue

        country_value = worksheet[f"{country_column}{row}"].value
        country = "" if country_value is None else str(country_value).strip()

        front = {
            "tl": read_cell_group(worksheet, groups["ftl"], row),
            "tr": read_cell_group(worksheet, groups["ftr"], row),
            "bl": read_cell_group(worksheet, groups["fbl"], row),
            "br": read_cell_group(worksheet, groups["fbr"], row),
        }
        back = {
            "tl": read_cell_group(worksheet, groups["rtl"], row),
            "tr": read_cell_group(worksheet, groups["rtr"], row),
            "bl": read_cell_group(worksheet, groups["rbl"], row),
            "br": read_cell_group(worksheet, groups["rbr"], row),
        }

        front_offset = Offset(
            x=read_offset_cell(
                worksheet,
                offset_columns["front_x"],
                row,
                default_front.x,
                "front X offset",
            ),
            y=read_offset_cell(
                worksheet,
                offset_columns["front_y"],
                row,
                default_front.y,
                "front Y offset",
            ),
        )
        back_offset = Offset(
            x=read_offset_cell(
                worksheet,
                offset_columns["back_x"],
                row,
                default_back.x,
                "back X offset",
            ),
            y=read_offset_cell(
                worksheet,
                offset_columns["back_y"],
                row,
                default_back.y,
                "back Y offset",
            ),
        )

        coins.append(
            Coin(
                diameter_mm=diameter_mm,
                country=country,
                front=front,
                back=back,
                front_offset=front_offset,
                back_offset=back_offset,
            )
        )

    workbook.close()
    return coins


# -----------------------------------------------------------------------------
# Drawing helpers
# -----------------------------------------------------------------------------

def draw_text_or_flag(
    pdf: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    primary_font: str,
    cjk_font: Optional[str],
    font_size: float,
    flag_folder: str,
    flag_width: float,
    flag_height: float,
    country: str,
    align_right: bool = False,
    align_top: bool = False,
) -> None:
    text_value = str(text).strip()
    font = cjk_font if cjk_font and contains_cjk(text_value) else primary_font

    if text_value.lower() == "[flag]":
        flag_path = os.path.join(flag_folder, f"{country.upper()}.png")
        if not os.path.isfile(flag_path):
            placeholder = f"[{country}]"
            pdf.setFont(primary_font, font_size)
            width = pdf.stringWidth(placeholder, primary_font, font_size)
            pdf.drawString(x - width if align_right else x, y, placeholder)
            return

        try:
            with Image.open(flag_path) as image:
                image_width, image_height = image.size
                aspect_ratio = image_width / image_height
        except Exception as exc:
            print(f"WARNING: Could not open flag '{flag_path}': {exc}")
            return

        target_ratio = flag_width / flag_height
        if aspect_ratio > target_ratio:
            display_width = flag_width
            display_height = flag_width / aspect_ratio
        else:
            display_height = flag_height
            display_width = flag_height * aspect_ratio

        draw_x = x - display_width if align_right else x
        draw_y = y - display_height if align_top else y
        pdf.drawImage(
            ImageReader(flag_path),
            draw_x,
            draw_y,
            width=display_width,
            height=display_height,
            preserveAspectRatio=True,
            mask="auto",
        )
        return

    pdf.setFont(font, font_size)
    text_width = pdf.stringWidth(text_value, font, font_size)
    pdf.drawString(x - text_width if align_right else x, y, text_value)


def draw_center_mark(
    pdf: canvas.Canvas,
    center_x: float,
    center_y: float,
    style: str,
    size: float,
    guide_color: Tuple[float, float, float],
) -> None:
    style = style.strip().lower()
    if style in {"", "none", "off"}:
        return

    pdf.saveState()
    pdf.setStrokeColorRGB(*guide_color)
    pdf.setFillColorRGB(*guide_color)

    if style == "x":
        half = size / 2.0
        pdf.line(center_x - half, center_y - half, center_x + half, center_y + half)
        pdf.line(center_x - half, center_y + half, center_x + half, center_y - half)
    elif style == "dot":
        # A small filled point, not an outlined circle.
        radius = max(0.35, size / 2.0)
        pdf.circle(center_x, center_y, radius, stroke=0, fill=1)
    else:
        print(f"WARNING: Unknown center_mark '{style}'; no centre mark drawn.")

    pdf.restoreState()


def draw_circle_guide(
    pdf: canvas.Canvas,
    center_x: float,
    center_y: float,
    radius: float,
    style: str,
    guide_size: float,
    guide_color: Tuple[float, float, float],
) -> None:
    style = style.strip().lower()
    if style in {"", "none", "off"}:
        return

    pdf.saveState()
    pdf.setStrokeColorRGB(*guide_color)
    pdf.setFillColorRGB(*guide_color)

    if style == "full":
        pdf.circle(center_x, center_y, radius, stroke=1, fill=0)

    elif style == "points":
        point_radius = max(0.35, guide_size / 2.0)
        points = (
            (center_x, center_y + radius),
            (center_x + radius, center_y),
            (center_x, center_y - radius),
            (center_x - radius, center_y),
        )
        for point_x, point_y in points:
            pdf.circle(point_x, point_y, point_radius, stroke=0, fill=1)

    elif style == "ticks":
        half = max(0.5, guide_size / 2.0)
        pdf.line(center_x, center_y + radius - half, center_x, center_y + radius + half)
        pdf.line(center_x + radius - half, center_y, center_x + radius + half, center_y)
        pdf.line(center_x, center_y - radius - half, center_x, center_y - radius + half)
        pdf.line(center_x - radius - half, center_y, center_x - radius + half, center_y)

    else:
        print(f"WARNING: Unknown circle_style '{style}'; no circle guide drawn.")

    pdf.restoreState()


def draw_corner(
    pdf: canvas.Canvas,
    holder_x: float,
    holder_y: float,
    holder_width: float,
    holder_height: float,
    lines: List[str],
    primary_font: str,
    cjk_font: Optional[str],
    font_size: float,
    padding: float,
    flag_folder: str,
    flag_width: float,
    flag_height: float,
    country: str,
    align_right: bool,
    align_top: bool,
) -> None:
    line_step = font_size + 1.0
    text_x = holder_x + (holder_width - padding if align_right else padding)

    if align_top:
        cursor_y = holder_y + holder_height - padding - font_size
        direction = -1.0
    else:
        cursor_y = holder_y + padding
        direction = 1.0

    for line in lines:
        draw_text_or_flag(
            pdf=pdf,
            text=line,
            x=text_x,
            y=cursor_y,
            primary_font=primary_font,
            cjk_font=cjk_font,
            font_size=font_size,
            flag_folder=flag_folder,
            flag_width=flag_width,
            flag_height=flag_height,
            country=country,
            align_right=align_right,
            align_top=align_top,
        )
        cursor_y += direction * line_step


def draw_holder_contents(
    pdf: canvas.Canvas,
    holder_x: float,
    holder_y: float,
    holder_width: float,
    holder_height: float,
    side: Dict[str, List[str]],
    coin: Coin,
    offset: Offset,
    primary_font: str,
    cjk_font: Optional[str],
    font_size: float,
    padding: float,
    flag_folder: str,
    flag_width: float,
    flag_height: float,
    circle_style: str,
    circle_guide_size: float,
    center_mark: str,
    center_mark_size: float,
    guide_color: Tuple[float, float, float],
) -> None:
    """
    Draw one side of a holder.

    The offset moves only the cutout guide and centre mark. The holder outline
    and all corner labels remain at their normal positions.
    """
    nominal_center_x = holder_x + holder_width / 2.0
    nominal_center_y = holder_y + holder_height / 2.0
    cutout_center_x = nominal_center_x + offset.x
    cutout_center_y = nominal_center_y + offset.y

    diameter_points = coin.diameter_mm * mm
    maximum_diameter = max(0.0, min(holder_width, holder_height) - 2.0 * padding)
    diameter_points = min(diameter_points, maximum_diameter)
    radius = diameter_points / 2.0

    draw_circle_guide(
        pdf,
        cutout_center_x,
        cutout_center_y,
        radius,
        circle_style,
        circle_guide_size,
        guide_color,
    )
    draw_center_mark(
        pdf,
        cutout_center_x,
        cutout_center_y,
        center_mark,
        center_mark_size,
        guide_color,
    )

    draw_corner(
        pdf, holder_x, holder_y, holder_width, holder_height, side["tl"],
        primary_font, cjk_font, font_size, padding, flag_folder,
        flag_width, flag_height, coin.country, False, True,
    )
    draw_corner(
        pdf, holder_x, holder_y, holder_width, holder_height, side["tr"],
        primary_font, cjk_font, font_size, padding, flag_folder,
        flag_width, flag_height, coin.country, True, True,
    )
    draw_corner(
        pdf, holder_x, holder_y, holder_width, holder_height, side["bl"],
        primary_font, cjk_font, font_size, padding, flag_folder,
        flag_width, flag_height, coin.country, False, False,
    )
    draw_corner(
        pdf, holder_x, holder_y, holder_width, holder_height, side["br"],
        primary_font, cjk_font, font_size, padding, flag_folder,
        flag_width, flag_height, coin.country, True, False,
    )


# -----------------------------------------------------------------------------
# PDF generation
# -----------------------------------------------------------------------------

def get_page_size(config: Dict[str, str]) -> Tuple[float, float]:
    page_name = config.get("page_size", "LETTER").strip().upper()
    page_size = getattr(pagesizes, page_name, None)
    if not isinstance(page_size, tuple) or len(page_size) != 2:
        print(f"WARNING: Unknown page_size '{page_name}'; using LETTER.")
        page_size = pagesizes.LETTER

    orientation = config.get("page_orientation", "landscape").strip().lower()
    if orientation == "portrait":
        return portrait(page_size)
    if orientation == "landscape":
        return landscape(page_size)

    print(f"WARNING: Unknown page_orientation '{orientation}'; using landscape.")
    return landscape(page_size)


def generate_pdf(coins: List[Coin], config: Dict[str, str], output_path: str) -> None:
    page_width, page_height = get_page_size(config)

    margin = parse_measurement(config.get("margin", "0.25in"))
    columns = int(config.get("cols", 4))
    holder_width = parse_measurement(config.get("holder_width", "2in"))
    holder_height = parse_measurement(config.get("holder_height", "2in"))
    vertical_spacing = parse_measurement(config.get("v_spacing", "0.3in"))

    if columns < 1:
        raise ValueError("cols must be at least 1.")
    if holder_width <= 0 or holder_height <= 0:
        raise ValueError("holder_width and holder_height must be greater than zero.")

    usable_width = page_width - 2.0 * margin
    usable_height = page_height - 2.0 * margin
    required_height = 2.0 * holder_height + vertical_spacing

    if required_height > usable_height:
        raise ValueError(
            "The two holders do not fit vertically on the selected page. "
            "Reduce holder_height, v_spacing, or margin; or change the page orientation."
        )

    horizontal_spacing_value = config.get("h_spacing", "auto").strip().lower()
    if horizontal_spacing_value == "auto":
        if columns == 1:
            horizontal_spacing = 0.0
        else:
            horizontal_spacing = (usable_width - columns * holder_width) / (columns - 1)
    else:
        horizontal_spacing = parse_measurement(horizontal_spacing_value)

    if columns * holder_width + (columns - 1) * horizontal_spacing > usable_width + 0.01:
        raise ValueError(
            "The configured columns do not fit across the page. Reduce cols, "
            "holder_width, h_spacing, or margin."
        )

    flag_folder = resolve_path(config.get("flag_folder", "flags")) or ""
    flag_width = parse_measurement(config.get("flag_width", "0.46in"))
    flag_height = parse_measurement(config.get("flag_height", "0.2875in"))

    primary_font = register_font(
        config.get("font_name", "Helvetica"),
        config.get("font_ttf_path", "").strip() or None,
    )
    cjk_font = select_cjk_font(coins, config, primary_font)
    font_size = float(config.get("font_size", 7))

    line_color = parse_rgb(config.get("line_color", "#000000"))
    line_width = float(config.get("line_width", 0.5))
    padding = parse_measurement(config.get("padding", "6pt"))
    center_mark = config.get("center_mark", "none")
    center_mark_size = parse_measurement(config.get("center_mark_size", "1pt"))
    circle_style = config.get("circle_style", "full")
    circle_guide_size = parse_measurement(config.get("circle_guide_size", "1pt"))

    pdf = canvas.Canvas(output_path, pagesize=(page_width, page_height))
    pdf.setTitle("Coin Holder Labels")

    index_on_page = 0
    page_number = 1

    def apply_page_style() -> None:
        pdf.setLineWidth(line_width)
        pdf.setStrokeColorRGB(*line_color)
        pdf.setFillColorRGB(0.0, 0.0, 0.0)  # Keep text black.

    apply_page_style()

    for coin_number, coin in enumerate(coins, start=1):
        if index_on_page == columns:
            pdf.showPage()
            page_number += 1
            index_on_page = 0
            apply_page_style()

        holder_x = margin + index_on_page * (holder_width + horizontal_spacing)
        lower_y = margin + (usable_height - required_height) / 2.0
        upper_y = lower_y + holder_height + vertical_spacing

        # Holder outlines remain centred and unshifted.
        pdf.rect(holder_x, lower_y, holder_width, holder_height, stroke=1, fill=0)
        pdf.rect(holder_x, upper_y, holder_width, holder_height, stroke=1, fill=0)

        # Front is the lower label; back is the upper label. Both are upright.
        draw_holder_contents(
            pdf=pdf,
            holder_x=holder_x,
            holder_y=lower_y,
            holder_width=holder_width,
            holder_height=holder_height,
            side=coin.front,
            coin=coin,
            offset=coin.front_offset,
            primary_font=primary_font,
            cjk_font=cjk_font,
            font_size=font_size,
            padding=padding,
            flag_folder=flag_folder,
            flag_width=flag_width,
            flag_height=flag_height,
            circle_style=circle_style,
            circle_guide_size=circle_guide_size,
            center_mark=center_mark,
            center_mark_size=center_mark_size,
            guide_color=line_color,
        )
        draw_holder_contents(
            pdf=pdf,
            holder_x=holder_x,
            holder_y=upper_y,
            holder_width=holder_width,
            holder_height=holder_height,
            side=coin.back,
            coin=coin,
            offset=coin.back_offset,
            primary_font=primary_font,
            cjk_font=cjk_font,
            font_size=font_size,
            padding=padding,
            flag_folder=flag_folder,
            flag_width=flag_width,
            flag_height=flag_height,
            circle_style=circle_style,
            circle_guide_size=circle_guide_size,
            center_mark=center_mark,
            center_mark_size=center_mark_size,
            guide_color=line_color,
        )

        print(
            f"Coin {coin_number}: front offset "
            f"({coin.front_offset.x / mm:+.2f} mm, {coin.front_offset.y / mm:+.2f} mm); "
            f"back offset ({coin.back_offset.x / mm:+.2f} mm, "
            f"{coin.back_offset.y / mm:+.2f} mm)"
        )
        index_on_page += 1

    pdf.save()
    print(f"PDF saved: {output_path}")
    print(f"Coins rendered: {len(coins)} across {page_number} page(s).")


# -----------------------------------------------------------------------------
# Program entry point
# -----------------------------------------------------------------------------

def main() -> int:
    print("EasyLabel coin-holder label generator")

    try:
        config_path = os.path.join(script_directory(), "config.txt")
        config = load_config(config_path)

        excel_path = resolve_path(config.get("excel_file", "coininfo.xlsx"))
        if not excel_path or not os.path.isfile(excel_path):
            raise FileNotFoundError(f"Excel file not found: {excel_path}")

        coins = read_coins_from_excel(excel_path, config)
        if not coins:
            raise ValueError(
                "No valid coin rows were found. Check start_row, end_row, and "
                "the configured diameter column."
            )

        output_path = resolve_path(config.get("output_file", "Coin_Holders.pdf"))
        if not output_path:
            raise ValueError("output_file cannot be blank.")

        generate_pdf(coins, config, output_path)
        return 0

    except PermissionError as exc:
        print(f"ERROR: Could not write the output file: {exc}")
        print("Close the existing PDF if it is open, then run the program again.")
        return 1
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}")
        return 1
    except Exception as exc:
        print(f"ERROR: Unexpected failure: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
