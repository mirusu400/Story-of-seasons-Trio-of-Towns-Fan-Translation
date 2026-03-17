#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
TOOLS_DIR = ROOT / "tools"
VENDORED_BFLIM = TOOLS_DIR / "bflim.py"

SARC_HEADER_LEN = 0x14
SFAT_HEADER_LEN = 0x0C
SFNT_HEADER_LEN = 0x08
SFAT_NODE_LEN = 0x10
SARC_MAGIC = b"SARC"
SFAT_MAGIC = b"SFAT"
SFNT_MAGIC = b"SFNT"
SARC_HEADER_UNKNOWN = 0x100
SFAT_HASH_MULTIPLIER = 0x65

TEXT_HINT_RE = re.compile(
    r"(title|logo|event|letter|mail|menu|select|option|save|load|guide|tutorial|"
    r"calendar|communication|chat|quest|memo|cook|shop|town|home|nix|property|"
    r"achievement|present|story|picture|target|choice|bookshelf|post|office|"
    r"wnd_title|comment|tutorial|recipe|rcp|ttl|select|receive|name)",
    re.IGNORECASE,
)


@dataclass
class SarcEntry:
    name: str | None
    data: bytes
    hash_value: int
    has_name: bool


@dataclass
class SarcArchive:
    order: str
    data_offset: int
    entries: list[SarcEntry]


def load_module(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def calc_sarc_hash(name):
    result = 0
    for char in name:
        result = ord(char) + (result * SFAT_HASH_MULTIPLIER)
        result &= 0xFFFFFFFF
    return result


def parse_sarc(path):
    data = Path(path).read_bytes()
    if data[:4] != SARC_MAGIC:
        raise ValueError(f"Not a SARC archive: {path}")

    bom = struct.unpack_from(">H", data, 6)[0]
    order = "<" if bom == 0xFFFE else ">"
    header_len = struct.unpack_from(order + "H", data, 4)[0]
    data_offset = struct.unpack_from(order + "I", data, 0x0C)[0]

    sfat_off = header_len
    magic, sfat_len, node_count, hash_mult = struct.unpack_from(order + "4s2HI", data, sfat_off)
    if magic != SFAT_MAGIC:
        raise ValueError(f"Invalid SFAT in {path}")
    if hash_mult != SFAT_HASH_MULTIPLIER:
        raise ValueError(f"Unexpected SFAT hash multiplier in {path}: {hash_mult}")

    raw_nodes = []
    nodes_off = sfat_off + sfat_len
    for index in range(node_count):
        hash_value, name_flags, start, end = struct.unpack_from(
            order + "4I", data, nodes_off + (index * SFAT_NODE_LEN)
        )
        has_name = (name_flags >> 24) != 0
        name_off = (name_flags & 0x00FFFFFF) * 4
        raw_nodes.append((hash_value, has_name, name_off, start, end))

    sfnt_off = nodes_off + (node_count * SFAT_NODE_LEN)
    magic, sfnt_len, _zero = struct.unpack_from(order + "4s2H", data, sfnt_off)
    if magic != SFNT_MAGIC:
        raise ValueError(f"Invalid SFNT in {path}")
    names_base = sfnt_off + sfnt_len

    entries = []
    for hash_value, has_name, name_off, start, end in raw_nodes:
        name = None
        if has_name:
            pos = names_base + name_off
            end_pos = data.index(b"\x00", pos)
            name = data[pos:end_pos].decode("utf-8", errors="replace")
        blob = data[data_offset + start : data_offset + end]
        entries.append(SarcEntry(name=name, data=blob, hash_value=hash_value, has_name=has_name))

    return SarcArchive(order=order, data_offset=data_offset, entries=entries)


def write_sarc(archive, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    order = archive.order
    bom = 0xFEFF if order == "<" else 0xFFFE

    names_blob = bytearray()
    nodes = []
    for entry in archive.entries:
        if entry.has_name and entry.name:
            name_offset_words = len(names_blob) // 4
            encoded = entry.name.encode("utf-8") + b"\x00"
            names_blob += encoded
            if len(names_blob) % 4:
                names_blob += b"\x00" * (4 - (len(names_blob) % 4))
            name_flags = 0x01000000 | name_offset_words
            hash_value = calc_sarc_hash(entry.name)
        else:
            name_flags = 0
            hash_value = entry.hash_value
        nodes.append([hash_value, name_flags, 0, 0, entry.data])

    header = struct.pack(
        order + "4s2H3I",
        SARC_MAGIC,
        SARC_HEADER_LEN,
        bom,
        0,
        0,
        SARC_HEADER_UNKNOWN,
    )
    sfat = struct.pack(order + "4s2HI", SFAT_MAGIC, SFAT_HEADER_LEN, len(nodes), SFAT_HASH_MULTIPLIER)
    sfnt = struct.pack(order + "4s2H", SFNT_MAGIC, SFNT_HEADER_LEN, 0)

    pre_data = bytearray()
    pre_data += header
    pre_data += sfat
    nodes_off = len(pre_data)
    pre_data += b"\x00" * (len(nodes) * SFAT_NODE_LEN)
    pre_data += sfnt
    pre_data += names_blob
    target_data_offset = archive.data_offset or 0x100
    if len(pre_data) > target_data_offset:
        target_data_offset = (len(pre_data) + 0x7F) & ~0x7F
    if len(pre_data) < target_data_offset:
        pre_data += b"\x00" * (target_data_offset - len(pre_data))
    data_offset = len(pre_data)

    data_blob = bytearray()
    node_bytes = bytearray()
    for hash_value, name_flags, _start, _end, blob in nodes:
        if len(data_blob) % 0x80:
            data_blob += b"\x00" * (0x80 - (len(data_blob) % 0x80))
        start = len(data_blob)
        data_blob += blob
        end = len(data_blob)
        node_bytes += struct.pack(order + "4I", hash_value, name_flags, start, end)

    total_size = data_offset + len(data_blob)
    packed_header = struct.pack(
        order + "4s2H3I",
        SARC_MAGIC,
        SARC_HEADER_LEN,
        bom,
        total_size,
        data_offset,
        SARC_HEADER_UNKNOWN,
    )
    pre_data[:SARC_HEADER_LEN] = packed_header
    pre_data[nodes_off : nodes_off + len(node_bytes)] = node_bytes

    with open(output_path, "wb") as f:
        f.write(pre_data)
        f.write(data_blob)


def pil_image_from_bflim(bflim_path):
    bflim_mod = load_module(VENDORED_BFLIM, "vendored_bflim")
    bflim = bflim_mod.Bflim()
    bflim.read(str(bflim_path), parse_image=True)
    if bflim.invalid:
        raise RuntimeError(f"Failed to parse BFLIM: {bflim_path}")
    image = Image.new("RGBA", (bflim.imag["width"], bflim.imag["height"]))
    image.putdata([tuple(map(int, pixel)) for pixel in bflim.bmp[: bflim.imag["width"] * bflim.imag["height"]]])
    return image, bflim


def replace_bflim_from_png(original_bflim, png_path):
    bflim_mod = load_module(VENDORED_BFLIM, "vendored_bflim")
    bflim = bflim_mod.Bflim()
    bflim.read(str(original_bflim), parse_image=True)
    if bflim.invalid:
        raise RuntimeError(f"Failed to parse BFLIM: {original_bflim}")

    image = Image.open(png_path).convert("RGBA")
    if image.size != (bflim.imag["width"], bflim.imag["height"]):
        raise ValueError(
            f"PNG size mismatch for {png_path}: expected {bflim.imag['width']}x{bflim.imag['height']}, "
            f"got {image.width}x{image.height}"
        )

    bflim.bmp = bflim._parse_image_data(list(image.getdata()), to_bin=True, exact=False)
    out_path = Path(png_path).with_suffix(".bflim")
    bflim.save(str(out_path))
    return out_path.read_bytes()


def archive_output_base(arc_path, out_root):
    arc_path = Path(arc_path)
    out_root = Path(out_root)
    return out_root / arc_path.stem


def extract_arc(arc_path, out_root):
    arc_path = Path(arc_path)
    out_dir = archive_output_base(arc_path, out_root)
    raw_dir = out_dir / "raw"
    png_dir = out_dir / "png"
    raw_dir.mkdir(parents=True, exist_ok=True)
    png_dir.mkdir(parents=True, exist_ok=True)

    archive = parse_sarc(arc_path)
    manifest = {
        "source_arc": str(arc_path),
        "order": archive.order,
        "entries": [],
    }

    for index, entry in enumerate(archive.entries):
        rel_name = entry.name or f"{index:04d}.noname.bin"
        raw_path = raw_dir / rel_name
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(entry.data)

        item = {
            "index": index,
            "name": entry.name,
            "has_name": entry.has_name,
            "hash": entry.hash_value,
            "raw_path": str(raw_path.relative_to(out_dir)),
            "kind": Path(rel_name).suffix.lower(),
        }

        if rel_name.endswith(".bflim"):
            png_path = png_dir / (Path(rel_name).with_suffix(".png"))
            png_path.parent.mkdir(parents=True, exist_ok=True)
            image, bflim = pil_image_from_bflim(raw_path)
            image.save(png_path)
            item["png_path"] = str(png_path.relative_to(out_dir))
            item["png_sha256"] = sha256_file(png_path)
            item["image"] = {
                "width": bflim.imag["width"],
                "height": bflim.imag["height"],
                "format": int(bflim.imag["format"]),
                "swizzle": int(bflim.imag["swizzle"]),
            }
        manifest["entries"].append(item)

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def repack_arc(extracted_dir, output_arc):
    extracted_dir = Path(extracted_dir)
    manifest = json.loads((extracted_dir / "manifest.json").read_text(encoding="utf-8"))
    archive = SarcArchive(order=manifest["order"], data_offset=0x100, entries=[])

    for entry_meta in manifest["entries"]:
        raw_path = extracted_dir / entry_meta["raw_path"]
        data = raw_path.read_bytes()
        if entry_meta.get("png_path"):
            png_path = extracted_dir / entry_meta["png_path"]
            if png_path.exists() and sha256_file(png_path) != entry_meta.get("png_sha256"):
                data = replace_bflim_from_png(raw_path, png_path)
        archive.entries.append(
            SarcEntry(
                name=entry_meta["name"],
                data=data,
                hash_value=int(entry_meta["hash"]),
                has_name=bool(entry_meta["has_name"]),
            )
        )

    write_sarc(archive, output_arc)


def score_archive(arc_path, entry_names):
    bflims = [name for name in entry_names if name.endswith(".bflim")]
    if not bflims:
        return 0
    score = 0
    if TEXT_HINT_RE.search(str(arc_path)):
        score += 3
    score += sum(2 for name in bflims if TEXT_HINT_RE.search(name))
    lower_names = [name.lower() for name in bflims]
    if any("logo" in name for name in lower_names):
        score += 4
    if any("event_" in name for name in lower_names):
        score += 4
    if any("letter" in name for name in lower_names):
        score += 4
    return score


def scan_candidates(layout_root):
    rows = []
    for arc_path in sorted(Path(layout_root).rglob("*.arc")):
        try:
            archive = parse_sarc(arc_path)
        except Exception:
            continue
        names = [entry.name or "" for entry in archive.entries]
        score = score_archive(arc_path, names)
        if score <= 0:
            continue
        bflims = [name for name in names if name.endswith(".bflim")]
        rows.append(
            {
                "path": str(arc_path),
                "score": score,
                "bflim_count": len(bflims),
                "sample_bflim": bflims[:8],
            }
        )
    rows.sort(key=lambda row: (-row["score"], row["path"]))
    return rows


def print_candidates(rows, limit):
    for row in rows[:limit]:
        print(f"{row['score']:>2}  {row['bflim_count']:>3}  {row['path']}")
        print("   " + ", ".join(row["sample_bflim"]))


def default_extract_set():
    return [
        "rom/ExtractedRomFS/Layout/TitleUpper.arc",
        "rom/ExtractedRomFS/Layout/TitleLower.arc",
        "rom/ExtractedRomFS/Layout/EventTitle.arc",
        "rom/ExtractedRomFS/Layout/LogoUpper.arc",
        "rom/ExtractedRomFS/Layout/LogoLower.arc",
        "rom/ExtractedRomFS/Layout/HomeNixSign.arc",
        "rom/ExtractedRomFS/Layout/PostOffice.arc",
        "rom/ExtractedRomFS/Layout/CommunicationRoom.arc",
    ]


def extract_default_set(out_root):
    paths = []
    for arc in default_extract_set():
        if Path(arc).exists():
            paths.append(str(extract_arc(arc, out_root)))
    return paths


def make_parser():
    parser = argparse.ArgumentParser(description="Scan, extract, and repack translatable UI graphics")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="List high-probability graphic translation candidates in Layout/")
    scan.add_argument(
        "--layout-root",
        default="rom/ExtractedRomFS/Layout",
        help="Root directory containing Layout .arc files",
    )
    scan.add_argument("--limit", type=int, default=80, help="Number of rows to print")
    scan.add_argument("--json", help="Optional path to save the scan result as JSON")

    extract = sub.add_parser("extract-arc", help="Extract one .arc archive to raw files and PNGs")
    extract.add_argument("arc", help="Input .arc file")
    extract.add_argument("out_root", help="Output root directory")

    repack = sub.add_parser("repack-arc", help="Repack one extracted archive directory back to .arc")
    repack.add_argument("extracted_dir", help="Directory created by extract-arc")
    repack.add_argument("output_arc", help="Output .arc path")

    extract_default = sub.add_parser(
        "extract-default-set",
        help="Extract a curated starter set of likely text-bearing UI graphics",
    )
    extract_default.add_argument("out_root", help="Output root directory")

    return parser


def main():
    args = make_parser().parse_args()

    if args.command == "scan":
        rows = scan_candidates(args.layout_root)
        print_candidates(rows, args.limit)
        if args.json:
            Path(args.json).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    if args.command == "extract-arc":
        manifest_path = extract_arc(args.arc, args.out_root)
        print(f"Extracted {args.arc} -> {manifest_path.parent}")
        return

    if args.command == "repack-arc":
        repack_arc(args.extracted_dir, args.output_arc)
        print(f"Repacked {args.output_arc}")
        return

    if args.command == "extract-default-set":
        paths = extract_default_set(args.out_root)
        print(f"Extracted {len(paths)} archives into {args.out_root}")
        return


if __name__ == "__main__":
    main()
