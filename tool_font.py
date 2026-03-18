#!/usr/bin/env python3
import argparse
import contextlib
import copy
import importlib.util
import json
import math
import shutil
import tempfile
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
VENDORED_BFFNT = ROOT / "tools" / "bffnt.py"
WORK_TMP_ROOT = ROOT / "work" / ".tmp"


@contextlib.contextmanager
def pushd(path):
    original = Path.cwd()
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        import os

        os.chdir(path)
        yield path
    finally:
        os.chdir(original)


def load_bffnt_module():
    spec = importlib.util.spec_from_file_location("vendored_bffnt", VENDORED_BFFNT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load vendored bffnt.py from {VENDORED_BFFNT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def managed_tempdir(prefix):
    WORK_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    tmp_dir = WORK_TMP_ROOT / f"{prefix}{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=False)
    try:
        yield tmp_dir
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def manifest_base_name(manifest_path):
    name = Path(manifest_path).name
    suffix = "_manifest.json"
    if not name.endswith(suffix):
        raise ValueError(f"Manifest filename must end with {suffix}: {manifest_path}")
    return name[: -len(suffix)]


def sheet_paths(asset_dir, base_name):
    return sorted(
        Path(asset_dir).glob(f"{base_name}_sheet*.png"),
        key=lambda path: int(path.stem.split("sheet", 1)[1]),
    )


def load_manifest(manifest_path):
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(manifest, manifest_path):
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, sort_keys=True)


def glyph_layout(manifest):
    texture = manifest["textureInfo"]
    sheet = texture["sheetInfo"]
    glyph = texture["glyph"]
    return {
        "cell_width": glyph["width"],
        "cell_height": glyph["height"],
        "sheet_width": sheet["width"],
        "sheet_height": sheet["height"],
        "cols": sheet["cols"],
        "rows": sheet["rows"],
        "per_sheet": sheet["cols"] * sheet["rows"],
        "color_format": sheet["colorFormat"],
    }


def validate_compatible_layout(base_manifest, donor_manifest):
    base = glyph_layout(base_manifest)
    donor = glyph_layout(donor_manifest)
    for key in (
        "cell_width",
        "cell_height",
        "sheet_width",
        "sheet_height",
        "cols",
        "rows",
        "color_format",
    ):
        if base[key] != donor[key]:
            raise ValueError(f"Incompatible BFFNT layout for {key}: {base[key]} != {donor[key]}")


def validate_glyph_transfer_layout(base_manifest, donor_manifest):
    base = glyph_layout(base_manifest)
    donor = glyph_layout(donor_manifest)
    for key in ("cell_width", "cell_height"):
        if base[key] != donor[key]:
            raise ValueError(f"Incompatible glyph cell layout for {key}: {base[key]} != {donor[key]}")


def glyph_rect(index, manifest):
    layout = glyph_layout(manifest)
    per_sheet = layout["per_sheet"]
    cols = layout["cols"]
    cell_width = layout["cell_width"]
    cell_height = layout["cell_height"]

    sheet_index = index // per_sheet
    slot_index = index % per_sheet
    x = (slot_index % cols) * cell_width
    y = (slot_index // cols) * cell_height
    return sheet_index, (x, y, x + cell_width, y + cell_height)


def load_sheet_images(asset_dir, manifest_path):
    base_name = manifest_base_name(manifest_path)
    manifest = load_manifest(manifest_path)
    expected = manifest["textureInfo"]["sheetCount"]
    images = []
    for index in range(expected):
        image_path = Path(asset_dir) / f"{base_name}_sheet{index}.png"
        if not image_path.exists():
            raise FileNotFoundError(f"Missing sheet image: {image_path}")
        images.append(Image.open(image_path).convert("RGBA"))
    return images


def save_sheet_images(images, output_dir, base_name):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, image in enumerate(images):
        image.save(output_dir / f"{base_name}_sheet{index}.png")


def ensure_sheet_count(images, manifest, required_index):
    layout = glyph_layout(manifest)
    per_sheet = layout["per_sheet"]
    required_sheets = (required_index // per_sheet) + 1
    while len(images) < required_sheets:
        images.append(
            Image.new("RGBA", (layout["sheet_width"], layout["sheet_height"]), (0, 0, 0, 0))
        )
    manifest["textureInfo"]["sheetCount"] = len(images)


def clear_glyph_cell(image, box):
    image.paste((0, 0, 0, 0), box)


def blank_width_metrics():
    return {"left": 0, "glyph": 0, "char": 0}


def extracted_manifest_from_bffnt(module, bffnt):
    glyph_widths = {}
    for cwdh in bffnt.cwdh_sections:
        for index in range(cwdh["start"], cwdh["end"] + 1):
            glyph_widths[str(index)] = cwdh["data"][index - cwdh["start"]]

    glyph_mapping = {}
    for cmap in bffnt.cmap_sections:
        if cmap["type"] == module.MAPPING_DIRECT:
            for code in range(cmap["start"], cmap["end"] + 1):
                glyph_mapping[chr(code)] = code - cmap["start"] + cmap["indexOffset"]
        elif cmap["type"] == module.MAPPING_TABLE:
            for code in range(cmap["start"], cmap["end"] + 1):
                index = cmap["indexTable"][code - cmap["start"]]
                if index != 0xFFFF:
                    glyph_mapping[chr(code)] = index
        elif cmap["type"] == module.MAPPING_SCAN:
            for code, index in cmap["entries"].items():
                glyph_mapping[code] = index

    return {
        "version": bffnt.version,
        "fileType": bffnt.filetype,
        "fontInfo": bffnt.font_info,
        "textureInfo": {
            "glyph": bffnt.tglp["glyph"],
            "sheetCount": bffnt.tglp["sheetCount"],
            "sheetInfo": {
                "cols": bffnt.tglp["sheet"]["cols"],
                "rows": bffnt.tglp["sheet"]["rows"],
                "width": bffnt.tglp["sheet"]["width"],
                "height": bffnt.tglp["sheet"]["height"],
                "colorFormat": module.PIXEL_FORMATS[bffnt.tglp["sheet"]["format"]],
            },
        },
        "glyphWidths": glyph_widths,
        "glyphMap": glyph_mapping,
    }


def extract_bffnt(input_bffnt, output_dir, ensure_ascii=False):
    module = load_bffnt_module()
    bffnt = module.Bffnt()
    bffnt.read(str(input_bffnt))
    if bffnt.invalid:
        raise RuntimeError(f"Failed to parse BFFNT: {input_bffnt}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = Path(input_bffnt).stem

    manifest = extracted_manifest_from_bffnt(module, bffnt)
    manifest_path = output_dir / f"{base_name}_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True, ensure_ascii=ensure_ascii)

    for index, sheet in enumerate(bffnt.tglp["sheets"]):
        image = Image.new("RGBA", (sheet["width"], sheet["height"]))
        image.putdata([tuple(pixel) for pixel in sheet["data"]])
        image.save(output_dir / f"{base_name}_sheet{index}.png")

    return manifest_path


def build_bffnt(manifest_path, output_bffnt):
    module = load_bffnt_module()
    manifest_path = Path(manifest_path).resolve()
    output_bffnt = Path(output_bffnt).resolve()
    output_bffnt.parent.mkdir(parents=True, exist_ok=True)

    source_base = manifest_base_name(manifest_path)
    target_base = output_bffnt.stem
    source_dir = manifest_path.parent

    with managed_tempdir("gm3_bffnt_build_") as tmp_dir:
        shutil.copy2(manifest_path, tmp_dir / f"{target_base}_manifest.json")

        for index, image_path in enumerate(sheet_paths(source_dir, source_base)):
            shutil.copy2(image_path, tmp_dir / f"{target_base}_sheet{index}.png")

        bffnt = module.Bffnt(load_order="<")
        with pushd(tmp_dir):
            bffnt.load(f"{target_base}_manifest.json")
            if bffnt.invalid:
                raise RuntimeError(f"Failed to load extracted assets from {manifest_path}")
            bffnt.save(str(output_bffnt))


def load_text_auto(path):
    path = Path(path)
    data = path.read_bytes()
    encodings = ("utf-8", "utf-16", "utf-16-le", "utf-16-be")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("auto", data, 0, len(data), f"Failed to decode {path}")


def ordered_unique_characters(text):
    seen = set()
    chars = []
    for char in text:
        if char in "\r\n\t\x00":
            continue
        if char in seen:
            continue
        seen.add(char)
        chars.append(char)
    return chars


def load_font_characters(font_path, font_index=0):
    from fontTools.ttLib import TTCollection, TTFont

    font_path = Path(font_path)
    if font_path.suffix.lower() == ".ttc":
        collection = TTCollection(str(font_path))
        ttfont = collection.fonts[font_index]
    else:
        ttfont = TTFont(str(font_path), fontNumber=font_index)

    cmap = ttfont.getBestCmap() or {}
    return [chr(codepoint) for codepoint in sorted(cmap.keys())]


def collect_requested_characters(
    font_file,
    chars_file=None,
    all_font_glyphs=False,
    hangul_only=False,
    font_index=0,
):
    if all_font_glyphs:
        characters = load_font_characters(font_file, font_index=font_index)
    elif chars_file:
        characters = ordered_unique_characters(load_text_auto(chars_file))
    else:
        raise ValueError("Either chars_file or all_font_glyphs must be provided")

    if hangul_only:
        characters = [char for char in characters if 0xAC00 <= ord(char) <= 0xD7A3]

    return characters


def rgba_from_alpha(alpha_image):
    rgba = Image.new("RGBA", alpha_image.size, (255, 255, 255, 0))
    rgba.putalpha(alpha_image)
    return rgba


def clamp_signed_byte(value):
    return max(-128, min(127, int(value)))


def render_glyph(char, pil_font, cell_width, cell_height, baseline, x_offset=0, y_offset=0):
    alpha = Image.new("L", (cell_width, cell_height), 0)
    draw = ImageDraw.Draw(alpha)
    rel_bbox = draw.textbbox((0, 0), char, font=pil_font, anchor="ls")

    shift_x = x_offset - min(0, rel_bbox[0])
    draw_y = baseline + y_offset
    draw.text((shift_x, draw_y), char, font=pil_font, fill=255, anchor="ls")

    bbox = alpha.getbbox()
    if bbox is None:
        left = 0
        glyph_width = 0
    else:
        left = bbox[0]
        glyph_width = bbox[2] - bbox[0]

    advance = pil_font.getlength(char) if hasattr(pil_font, "getlength") else glyph_width
    char_width = max(glyph_width, int(math.ceil(advance)))
    metrics = {
        "left": clamp_signed_byte(left),
        "glyph": clamp_signed_byte(glyph_width),
        "char": clamp_signed_byte(char_width),
    }
    return rgba_from_alpha(alpha), metrics


def render_font_glyphs(
    base_manifest_path,
    font_file,
    output_dir,
    characters=None,
    chars_file=None,
    all_font_glyphs=False,
    hangul_only=False,
    font_index=0,
    font_size=None,
    x_offset=0,
    y_offset=0,
    start_at_new_sheet=True,
    replace_existing=False,
    preserve_existing_metrics=False,
    metric_overrides=None,
):
    base_manifest_path = Path(base_manifest_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    base_manifest = load_manifest(base_manifest_path)
    base_images = load_sheet_images(base_manifest_path.parent, base_manifest_path)

    merged_manifest = copy.deepcopy(base_manifest)
    merged_manifest["glyphWidths"] = {
        str(key): value for key, value in merged_manifest["glyphWidths"].items()
    }
    merged_images = [image.copy() for image in base_images]

    layout = glyph_layout(merged_manifest)
    base_chars = set(merged_manifest["glyphMap"].keys())

    if characters is None:
        characters = collect_requested_characters(
            font_file,
            chars_file=chars_file,
            all_font_glyphs=all_font_glyphs,
            hangul_only=hangul_only,
            font_index=font_index,
        )

    if not replace_existing:
        characters = [char for char in characters if char not in base_chars]

    pil_font = ImageFont.truetype(
        str(font_file),
        size=font_size or layout["cell_height"],
        index=font_index,
    )
    metric_overrides = metric_overrides or {}

    next_index = max((int(key) for key in merged_manifest["glyphWidths"].keys()), default=-1) + 1
    if start_at_new_sheet and next_index % layout["per_sheet"] != 0:
        aligned_index = ((next_index + layout["per_sheet"] - 1) // layout["per_sheet"]) * layout["per_sheet"]
        for skipped_index in range(next_index, aligned_index):
            ensure_sheet_count(merged_images, merged_manifest, skipped_index)
            skipped_sheet, skipped_box = glyph_rect(skipped_index, merged_manifest)
            clear_glyph_cell(merged_images[skipped_sheet], skipped_box)
            merged_manifest["glyphWidths"][str(skipped_index)] = blank_width_metrics()
        next_index = aligned_index
    for char in characters:
        glyph_bitmap, metrics = render_glyph(
            char,
            pil_font,
            layout["cell_width"],
            layout["cell_height"],
            merged_manifest["textureInfo"]["glyph"]["baseline"],
            x_offset=x_offset,
            y_offset=y_offset,
        )
        if replace_existing and char in merged_manifest["glyphMap"]:
            target_index = merged_manifest["glyphMap"][char]
        else:
            target_index = next_index
            ensure_sheet_count(merged_images, merged_manifest, target_index)
            next_index += 1

        target_sheet, target_box = glyph_rect(target_index, merged_manifest)
        clear_glyph_cell(merged_images[target_sheet], target_box)
        merged_images[target_sheet].paste(glyph_bitmap, target_box)
        merged_manifest["glyphMap"][char] = target_index
        if not (preserve_existing_metrics and char in base_chars):
            merged_manifest["glyphWidths"][str(target_index)] = copy.deepcopy(
                metric_overrides.get(char, metrics)
            )

    output_base = manifest_base_name(base_manifest_path)
    output_manifest_path = output_dir / f"{output_base}_manifest.json"
    save_manifest(merged_manifest, output_manifest_path)
    save_sheet_images(merged_images, output_dir, output_base)

    return {
        "manifest": output_manifest_path,
        "added": characters,
        "sheet_count": merged_manifest["textureInfo"]["sheetCount"],
        "glyph_count": len(merged_manifest["glyphWidths"]),
    }


def donor_characters(base_manifest, donor_manifest, hangul_only=False, include_existing=False):
    base_chars = set(base_manifest["glyphMap"].keys())
    chars = []
    for char, donor_index in sorted(donor_manifest["glyphMap"].items(), key=lambda item: item[1]):
        if not include_existing and char in base_chars:
            continue
        if hangul_only and not (0xAC00 <= ord(char) <= 0xD7A3):
            continue
        chars.append(char)
    return chars


def merge_donor_assets(
    base_manifest_path,
    donor_manifest_path,
    output_dir,
    hangul_only=False,
    replace_existing=False,
):
    base_manifest_path = Path(base_manifest_path).resolve()
    donor_manifest_path = Path(donor_manifest_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    base_manifest = load_manifest(base_manifest_path)
    donor_manifest = load_manifest(donor_manifest_path)
    validate_glyph_transfer_layout(base_manifest, donor_manifest)

    base_images = load_sheet_images(base_manifest_path.parent, base_manifest_path)
    donor_images = load_sheet_images(donor_manifest_path.parent, donor_manifest_path)

    merged_manifest = copy.deepcopy(base_manifest)
    merged_images = [image.copy() for image in base_images]
    merged_manifest["glyphWidths"] = {
        str(key): value for key, value in merged_manifest["glyphWidths"].items()
    }
    merged_widths = merged_manifest["glyphWidths"]
    next_index = max((int(key) for key in merged_widths.keys()), default=-1) + 1

    transferred_chars = donor_characters(
        base_manifest,
        donor_manifest,
        hangul_only=hangul_only,
        include_existing=replace_existing,
    )
    for char in transferred_chars:
        donor_index = donor_manifest["glyphMap"][char]
        donor_widths = donor_manifest["glyphWidths"].get(str(donor_index))
        if donor_widths is None:
            raise KeyError(f"Missing donor glyph width for index {donor_index} ({char!r})")

        source_sheet, source_box = glyph_rect(donor_index, donor_manifest)
        if replace_existing and char in merged_manifest["glyphMap"]:
            target_index = merged_manifest["glyphMap"][char]
        else:
            target_index = next_index
            ensure_sheet_count(merged_images, merged_manifest, target_index)
            next_index += 1

        target_sheet, target_box = glyph_rect(target_index, merged_manifest)
        glyph_bitmap = donor_images[source_sheet].crop(source_box)
        clear_glyph_cell(merged_images[target_sheet], target_box)
        merged_images[target_sheet].paste(glyph_bitmap, target_box)

        merged_manifest["glyphMap"][char] = target_index
        merged_widths[str(target_index)] = copy.deepcopy(donor_widths)

    output_base = manifest_base_name(base_manifest_path)
    output_manifest_path = output_dir / f"{output_base}_manifest.json"
    save_manifest(merged_manifest, output_manifest_path)
    save_sheet_images(merged_images, output_dir, output_base)

    return {
        "manifest": output_manifest_path,
        "added": transferred_chars,
        "sheet_count": merged_manifest["textureInfo"]["sheetCount"],
        "glyph_count": len(merged_manifest["glyphWidths"]),
    }


def rebase_assets_to_reference_layout(base_manifest_path, reference_manifest_path, output_dir):
    base_manifest_path = Path(base_manifest_path).resolve()
    reference_manifest_path = Path(reference_manifest_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    base_manifest = load_manifest(base_manifest_path)
    reference_manifest = load_manifest(reference_manifest_path)
    validate_glyph_transfer_layout(base_manifest, reference_manifest)

    base_images = load_sheet_images(base_manifest_path.parent, base_manifest_path)
    rebased_manifest = copy.deepcopy(base_manifest)
    rebased_manifest["textureInfo"]["sheetInfo"] = copy.deepcopy(
        reference_manifest["textureInfo"]["sheetInfo"]
    )
    rebased_manifest["textureInfo"]["sheetCount"] = 0

    max_index = max((int(index) for index in rebased_manifest["glyphWidths"].keys()), default=-1)
    rebased_images = []
    if max_index >= 0:
        ensure_sheet_count(rebased_images, rebased_manifest, max_index)

    for char, source_index in sorted(base_manifest["glyphMap"].items(), key=lambda item: item[1]):
        source_sheet, source_box = glyph_rect(source_index, base_manifest)
        target_sheet, target_box = glyph_rect(source_index, rebased_manifest)
        glyph_bitmap = base_images[source_sheet].crop(source_box)
        clear_glyph_cell(rebased_images[target_sheet], target_box)
        rebased_images[target_sheet].paste(glyph_bitmap, target_box)

    output_base = manifest_base_name(base_manifest_path)
    output_manifest_path = output_dir / f"{output_base}_manifest.json"
    save_manifest(rebased_manifest, output_manifest_path)
    save_sheet_images(rebased_images, output_dir, output_base)

    return {
        "manifest": output_manifest_path,
        "sheet_count": rebased_manifest["textureInfo"]["sheetCount"],
        "glyph_count": len(rebased_manifest["glyphWidths"]),
    }


def build_korean_mainfont(base_bffnt, output_bffnt, donor_dir, keep_dir=None, hangul_only=False):
    donor_dir = Path(donor_dir).resolve()
    donor_manifest = donor_dir / "mainfont_manifest.json"
    if not donor_manifest.exists():
        raise FileNotFoundError(f"Missing donor manifest: {donor_manifest}")

    with managed_tempdir("gm3_font_merge_") as tmp_dir:
        extracted_dir = tmp_dir / "base"
        merged_dir = tmp_dir / "merged"

        base_manifest = extract_bffnt(base_bffnt, extracted_dir)
        result = merge_donor_assets(
            base_manifest, donor_manifest, merged_dir, hangul_only=hangul_only
        )
        build_bffnt(result["manifest"], output_bffnt)

        if keep_dir:
            keep_dir = Path(keep_dir).resolve()
            if keep_dir.exists():
                shutil.rmtree(keep_dir)
            shutil.copytree(merged_dir, keep_dir)

    return result


def build_font_from_file(
    base_bffnt,
    font_file,
    output_bffnt,
    chars_file=None,
    all_font_glyphs=False,
    keep_dir=None,
    hangul_only=False,
    font_index=0,
    font_size=None,
    x_offset=0,
    y_offset=0,
    start_at_new_sheet=True,
):
    requested_characters = collect_requested_characters(
        font_file,
        chars_file=chars_file,
        all_font_glyphs=all_font_glyphs,
        hangul_only=hangul_only,
        font_index=font_index,
    )
    bundled_manifest = ROOT / "font" / f"{Path(base_bffnt).stem}_manifest.json"
    characters_to_render = requested_characters
    metric_overrides = {}

    with managed_tempdir("gm3_font_render_") as tmp_dir:
        extracted_dir = tmp_dir / "base"
        rebased_base_dir = tmp_dir / "rebased_base"
        rendered_dir = tmp_dir / "rendered"

        extracted_base_manifest = extract_bffnt(base_bffnt, extracted_dir)
        extracted_base_data = load_manifest(extracted_base_manifest)
        extracted_base_chars = set(extracted_base_data["glyphMap"].keys())
        characters_to_render = [
            char for char in requested_characters if char not in extracted_base_chars
        ]

        if bundled_manifest.exists():
            bundled_data = load_manifest(bundled_manifest)
            if all(char in bundled_data["glyphMap"] for char in characters_to_render):
                rebase_result = rebase_assets_to_reference_layout(
                    extracted_base_manifest,
                    bundled_manifest,
                    rebased_base_dir,
                )
                base_manifest = rebase_result["manifest"]
                for char in characters_to_render:
                    donor_index = bundled_data["glyphMap"][char]
                    metric_overrides[char] = copy.deepcopy(
                        bundled_data["glyphWidths"][str(donor_index)]
                    )
            else:
                base_manifest = extracted_base_manifest
        else:
            base_manifest = extracted_base_manifest
        result = render_font_glyphs(
            base_manifest,
            font_file,
            rendered_dir,
            characters=characters_to_render,
            chars_file=chars_file,
            all_font_glyphs=all_font_glyphs,
            hangul_only=hangul_only,
            font_index=font_index,
            font_size=font_size,
            x_offset=x_offset,
            y_offset=y_offset,
            start_at_new_sheet=start_at_new_sheet,
            metric_overrides=metric_overrides,
        )
        build_bffnt(result["manifest"], output_bffnt)

        if keep_dir:
            keep_dir = Path(keep_dir).resolve()
            if keep_dir.exists():
                shutil.rmtree(keep_dir)
            shutil.copytree(rendered_dir, keep_dir)

    return result


def make_parser():
    parser = argparse.ArgumentParser(description="BFFNT extract/build/merge helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="Extract a BFFNT to manifest + PNG sheets")
    extract_parser.add_argument("input_bffnt", help="Input .bffnt file")
    extract_parser.add_argument("output_dir", help="Directory to write extracted assets")

    build_parser = subparsers.add_parser("build", help="Build a BFFNT from manifest + PNG sheets")
    build_parser.add_argument("manifest", help="Path to *_manifest.json")
    build_parser.add_argument("output_bffnt", help="Output .bffnt file")

    merge_parser = subparsers.add_parser(
        "merge-donor", help="Append donor-only glyphs from one extracted font into another"
    )
    merge_parser.add_argument("base_manifest", help="Path to base *_manifest.json")
    merge_parser.add_argument("donor_manifest", help="Path to donor *_manifest.json")
    merge_parser.add_argument("output_dir", help="Directory to write merged manifest + sheets")
    merge_parser.add_argument(
        "--hangul-only",
        action="store_true",
        help="Only append donor Hangul syllables (U+AC00..U+D7A3)",
    )

    korean_parser = subparsers.add_parser(
        "build-korean-mainfont",
        help="Extract base mainfont.bffnt, append donor Korean glyphs, and rebuild it",
    )
    korean_parser.add_argument("base_bffnt", help="Original mainfont.bffnt")
    korean_parser.add_argument("output_bffnt", help="Output merged mainfont.bffnt")
    korean_parser.add_argument(
        "--donor-dir",
        default=str(ROOT / "font"),
        help="Directory containing mainfont_manifest.json and mainfont_sheet*.png",
    )
    korean_parser.add_argument(
        "--keep-dir",
        help="Optional directory to keep merged manifest + PNG sheets",
    )
    korean_parser.add_argument(
        "--hangul-only",
        action="store_true",
        help="Only append Hangul donor glyphs instead of all donor-only characters",
    )

    font_parser = subparsers.add_parser(
        "build-from-font",
        help="Extract a base BFFNT, render glyphs from a TTF/OTF/TTC, and rebuild it",
    )
    font_parser.add_argument("base_bffnt", help="Original base .bffnt")
    font_parser.add_argument("font_file", help="Input .ttf/.otf/.ttc font file")
    font_parser.add_argument("output_bffnt", help="Output merged .bffnt")
    font_parser.add_argument(
        "--chars-file",
        default=str(ROOT / "font" / "font_raw.txt"),
        help="Text file containing characters to append",
    )
    font_parser.add_argument(
        "--all-font-glyphs",
        action="store_true",
        help="Enumerate characters from the font cmap instead of using --chars-file",
    )
    font_parser.add_argument(
        "--hangul-only",
        action="store_true",
        help="Filter the selected characters down to Hangul syllables only",
    )
    font_parser.add_argument(
        "--font-index",
        type=int,
        default=0,
        help="Font index inside a TTC/collection file",
    )
    font_parser.add_argument(
        "--font-size",
        type=int,
        help="Pillow render size in pixels. Defaults to the base glyph cell height.",
    )
    font_parser.add_argument("--x-offset", type=int, default=0, help="Horizontal render offset")
    font_parser.add_argument("--y-offset", type=int, default=0, help="Vertical render offset")
    font_parser.add_argument(
        "--reuse-partial-sheet",
        action="store_true",
        help="Continue writing into the base font's last partially used sheet instead of starting on a fresh sheet",
    )
    font_parser.add_argument(
        "--keep-dir",
        help="Optional directory to keep rendered manifest + PNG sheets",
    )

    return parser


def main():
    parser = make_parser()
    args = parser.parse_args()

    if args.command == "extract":
        manifest_path = extract_bffnt(args.input_bffnt, args.output_dir)
        print(f"Extracted to {manifest_path.parent}")
        return

    if args.command == "build":
        build_bffnt(args.manifest, args.output_bffnt)
        print(f"Built {args.output_bffnt}")
        return

    if args.command == "merge-donor":
        result = merge_donor_assets(
            args.base_manifest,
            args.donor_manifest,
            args.output_dir,
            hangul_only=args.hangul_only,
        )
        print(
            f"Merged {len(result['added'])} donor glyphs into {result['manifest']} "
            f"({result['glyph_count']} glyphs across {result['sheet_count']} sheets)"
        )
        return

    if args.command == "build-korean-mainfont":
        result = build_korean_mainfont(
            args.base_bffnt,
            args.output_bffnt,
            args.donor_dir,
            keep_dir=args.keep_dir,
            hangul_only=args.hangul_only,
        )
        print(
            f"Built {args.output_bffnt} with {len(result['added'])} appended donor glyphs "
            f"({result['glyph_count']} glyphs across {result['sheet_count']} sheets)"
        )
        return

    if args.command == "build-from-font":
        result = build_font_from_file(
            args.base_bffnt,
            args.font_file,
            args.output_bffnt,
            chars_file=args.chars_file,
            all_font_glyphs=args.all_font_glyphs,
            keep_dir=args.keep_dir,
            hangul_only=args.hangul_only,
            font_index=args.font_index,
            font_size=args.font_size,
            x_offset=args.x_offset,
            y_offset=args.y_offset,
            start_at_new_sheet=not args.reuse_partial_sheet,
        )
        print(
            f"Built {args.output_bffnt} with {len(result['added'])} rendered glyphs "
            f"({result['glyph_count']} glyphs across {result['sheet_count']} sheets)"
        )
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
