from __future__ import annotations

from io import BytesIO
from pathlib import Path
import math
import shutil
from xml.sax.saxutils import escape

from PIL import Image, ImageFont
import pysubs2  # type: ignore[import-untyped]

from auraly_pipeline.editing.batch_domain import CaptionInput
from auraly_pipeline.editing.domain import EditManifestV2, EditingError, TextStyle
from auraly_pipeline.editing.render_domain import RenderRuntime, validate_supported
from auraly_pipeline.editing.render_runtime import invoke_ffmpeg


def escape_ass_text(text: str) -> str:
    # Word joiner is invisible and prevents literal \N/\n/\h from being commands.
    return (text.replace("\\", "\\\u2060").replace("{", "\\{").replace("}", "\\}")
            .replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\N"))


def _color(value: str) -> pysubs2.Color:
    return pysubs2.Color(int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16),
                        255 - (int(value[7:9], 16) if len(value) == 9 else 255))


def _family(path: Path) -> str:
    family = ImageFont.truetype(str(path), size=12).getname()[0]
    if not family:
        raise EditingError("text.font", "font family cannot be identified")
    return family


def _tags(style: TextStyle) -> str:
    color = _color(style.shadow_color)
    return (rf"\q2\xshad{style.shadow_offset_x if style.shadow_enabled else 0:g}"
            rf"\yshad{style.shadow_offset_y if style.shadow_enabled else 0:g}"
            rf"\4c&H{color.b:02X}{color.g:02X}{color.r:02X}&\4a&H{color.a:02X}&")


def _document(style: TextStyle, family: str, size: float, width: int, height: int) -> pysubs2.SSAFile:
    doc = pysubs2.SSAFile()
    doc.info.update({"PlayResX": str(width), "PlayResY": str(height),
                     "LayoutResX": str(width), "LayoutResY": str(height),
                     "WrapStyle": "2", "ScaledBorderAndShadow": "yes", "YCbCr Matrix": "None"})
    doc.styles["Default"] = pysubs2.SSAStyle(fontname=family, fontsize=size,
        primarycolor=_color(style.color), outlinecolor=_color(style.stroke_color),
        outline=style.stroke_width_px, shadow=0, bold=style.font_weight == 700,
        alignment=pysubs2.Alignment.TOP_LEFT, marginl=0, marginr=0, marginv=0)
    return doc


def _event(doc: pysubs2.SSAFile, text: str, tags: str, *, start: float = 0,
           end: float = 2, layer: int = 0) -> None:
    doc.events.append(pysubs2.SSAEvent(start=round(start * 1000), end=round(end * 1000),
                                      text="{" + tags + "}" + text, layer=layer))


def measure_ass(script: str, *, staging: Path, runtime: RenderRuntime) -> tuple[int, int, int, int]:
    del runtime  # Caller pins runtime in identity; rasterization uses that local executable.
    path = staging / "measure.ass"
    path.write_text(script, encoding="utf-8")
    parsed = pysubs2.SSAFile.from_string(script)
    width, height = int(parsed.info["PlayResX"]), int(parsed.info["PlayResY"])
    if width > 8192 or height > 4096:
        raise EditingError("text.fit", "text measurement exceeds local canvas limit")
    result = invoke_ffmpeg(["-v", "info", "-f", "lavfi", "-i",
        f"color=c=black@0:s={width}x{height}:r=1:d=1,format=rgba",
        "-vf", "ass=filename=measure.ass:fontsdir=fonts:alpha=1", "-frames:v", "1",
        "-f", "image2pipe", "-c:v", "png", "-"], cwd=staging, timeout_sec=30)
    log = result.stderr.decode("utf-8", errors="replace")
    if "fontselect:" not in log or "Glyph" in log or "failed to find" in log.lower():
        raise EditingError("text.font", "local font selection or glyph coverage failed")
    try:
        with Image.open(BytesIO(result.stdout)) as image:
            bbox = image.getchannel("A").getbbox()
            if bbox is None:
                return (0, 0, 0, 0)
            if bbox[0] <= 0 or bbox[1] <= 0 or bbox[2] >= width or bbox[3] >= height:
                raise EditingError("text.fit", "measurement would clip text")
            return bbox
    except (OSError, ValueError):
        raise EditingError("text.fit", "cannot measure local text raster") from None


def _lines(text: str, font: ImageFont.FreeTypeFont, max_width: float, fit: str) -> list[str]:
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if fit == "error":
            lines.append(paragraph)
            continue
        line = ""
        for word in paragraph.split():
            candidate = line + " " + word if line else word
            if line and font.getlength(candidate) > max_width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines


def _block(text: str, style: TextStyle, font_path: Path, staging: Path,
           runtime: RenderRuntime) -> tuple[float, list[str], list[tuple[int, int, int, int]], float]:
    size = style.font_size_px
    family = _family(font_path)
    available = 1080 * (1 - style.safe_left - style.safe_right)
    pad = style.background_padding_px if style.background_enabled else 0
    while size >= 1:
        font = ImageFont.truetype(str(font_path), size=max(1, math.ceil(size)))
        lines = _lines(text, font, max(1, available - 2 * pad), style.fit_policy)
        step = size * style.line_height
        boxes: list[tuple[int, int, int, int]] = []
        if len(lines) <= style.max_lines:
            for line in lines:
                if not line.strip():
                    boxes.append((0, 0, 0, 0))
                    continue
                # Conservative font metrics size the probe; libass pixels decide final fit.
                width = max(2048, math.ceil(font.getlength(line) * 2 + 512))
                height = max(512, math.ceil(size * 4 + abs(style.shadow_offset_y) * 2 + 256))
                doc = _document(style, family, size, width, height)
                _event(doc, escape_ass_text(line), _tags(style) + r"\pos(256,128)")
                boxes.append(measure_ass(doc.to_string("ass"), staging=staging, runtime=runtime))
            total_height = max((i * step + b[3] - b[1] for i, b in enumerate(boxes)), default=0) + 2 * pad
            total_width = max((b[2] - b[0] for b in boxes), default=0) + 2 * pad
            x = style.x * 1080
            y = style.y * 1920
            top = y if style.anchor == "top" else y - total_height * (1 if style.anchor == "bottom" else .5)
            if (x - total_width / 2 >= style.safe_left * 1080
                    and x + total_width / 2 <= (1 - style.safe_right) * 1080
                    and top >= style.safe_top * 1920
                    and top + total_height <= (1 - style.safe_bottom) * 1920):
                return size, lines, boxes, top + pad
        if style.fit_policy != "shrink":
            break
        size -= 1
    raise EditingError("text.fit", "text does not fit safe zones and maxLines")


def write_ass(manifest: EditManifestV2, caption: CaptionInput, *, font_paths: dict[str, Path],
              staging: Path, runtime: RenderRuntime) -> Path:
    validate_supported(manifest, caption)
    fonts = staging / "fonts"
    fonts.mkdir()
    cache = staging / "font-cache"
    cache.mkdir()
    families: dict[str, str] = {}
    for field, path in font_paths.items():
        family = _family(path)
        if family in families and families[family] != str(path):
            # Two faces with the same family can be resolved ambiguously by libass.
            raise EditingError("text.font", "ambiguous local font family")
        families[family] = str(path)
        shutil.copyfile(path, fonts / (field + path.suffix))
    (staging / "fontconfig.xml").write_text(
        "<?xml version='1.0'?><fontconfig><dir>" + escape(fonts.as_posix())
        + "</dir><cachedir>" + escape(cache.as_posix()) + "</cachedir></fontconfig>", encoding="utf-8")
    doc = _document(manifest.headline, "Arial", 60, 1080, 1920)
    blocks: list[tuple[str, TextStyle, str, float, float, int]] = []
    if manifest.headline.enabled:
        headline = manifest.headline
        blocks.append((headline.text, headline, "headline", headline.start_sec, headline.end_sec, 20))
    if manifest.captions.enabled:
        blocks.extend((cue.text, manifest.captions, "captions", cue.start_sec, cue.end_sec, 10)
                      for cue in caption.cues)
    for text, style, field, start, end, layer in blocks:
        font_path = font_paths[field]
        size, lines, boxes, top = _block(text, style, font_path, staging, runtime)
        family = _family(font_path)
        doc.styles[field] = _document(style, family, size, 1080, 1920).styles["Default"]
        padding = style.background_padding_px if style.background_enabled else 0
        width = max((b[2] - b[0] for b in boxes), default=0)
        height = max((i * size * style.line_height + b[3] - b[1]
                      for i, b in enumerate(boxes)), default=0)
        if style.background_enabled:
            color = _color(style.background_color)
            left, upper = style.x * 1080 - width / 2 - padding, top - padding
            w, h = width + 2 * padding, height + 2 * padding
            tags = (rf"\an7\pos({left:g},{upper:g})\bord0\shad0\p1"
                    rf"\1c&H{color.b:02X}{color.g:02X}{color.r:02X}&\1a&H{color.a:02X}&")
            _event(doc, f"m 0 0 l {w:g} 0 {w:g} {h:g} 0 {h:g}", tags,
                   start=start, end=end, layer=layer - 1)
        for i, (line, box) in enumerate(zip(lines, boxes, strict=True)):
            # Align actual visible raster bbox, not font ascender whitespace.
            x = style.x * 1080 - (box[2] - box[0]) / 2 - (box[0] - 256)
            y = top + i * size * style.line_height - (box[1] - 128)
            _event(doc, escape_ass_text(line), _tags(style) + rf"\an7\pos({x:g},{y:g})",
                   start=start, end=end, layer=layer)
            doc.events[-1].style = field
    path = staging / "overlay.ass"
    doc.save(str(path))
    return path
