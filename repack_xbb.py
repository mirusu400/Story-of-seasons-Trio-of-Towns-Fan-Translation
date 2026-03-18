import struct
import os
import json
import glob


def pad_text_bytes(b, enc):
    if enc in ("utf-16", "utf-16-le", "utf16"):
        b += b"\x00\x00"
    else:
        b += b"\x00"
    while len(b) % 4 != 0:
        b += b"\x00"
    return b


def encode_text(text, enc):
    if enc in ("utf-16", "utf-16-le", "utf16"):
        b = text.encode("utf-16-le")
        return pad_text_bytes(b, "utf-16")
    if enc in ("shift-jis", "shift_jis", "sjis"):
        b = text.encode("shift_jis")
        return pad_text_bytes(b, "shift-jis")
    b = text.encode("ascii")
    return pad_text_bytes(b, "ascii")


def check_encoding_raw(btext):
    count = 0
    for char in btext:
        if char == 0x0:
            count += 1
            continue
        if 0x30 <= char <= 0x7E:
            continue
        return "utf-16"
    if count >= 5:
        return "utf-16"
    return "ascii"


def parse_papa_blocks(papa_path):
    blocks = []
    with open(papa_path, "rb") as f:
        if f.read(4) != b"PAPA":
            raise ValueError(f"Not a PAPA file: {papa_path}")

        f.seek(0x0C)
        _header_size = struct.unpack("<I", f.read(4))[0]
        block_count = struct.unpack("<I", f.read(4))[0]

        offsets = []
        for _ in range(block_count):
            offsets.append(struct.unpack("<I", f.read(4))[0])

        file_size = os.path.getsize(papa_path)

        for i, block_offset in enumerate(offsets):
            if i == len(offsets) - 1:
                block_size = file_size - block_offset
            else:
                block_size = offsets[i + 1] - block_offset

            f.seek(block_offset)
            header = f.read(8)
            if len(header) < 8:
                blocks.append({"raw": [], "is_dummy": True})
                continue

            _blocksize, offsetcount = struct.unpack("<II", header)
            if offsetcount == 0:
                blocks.append({"raw": [], "is_dummy": True})
                continue

            sub_offsets = []
            for _ in range(offsetcount):
                sub_offsets.append(struct.unpack("<I", f.read(4))[0])

            sizes = []
            for j in range(len(sub_offsets)):
                if j != 0:
                    sizes.append(sub_offsets[j] - sub_offsets[j - 1])
            sizes.append(block_size - sub_offsets[-1])

            raw_strings = []
            for j, sub_off in enumerate(sub_offsets):
                f.seek(block_offset + sub_off)
                raw = f.read(sizes[j])
                raw_strings.append(raw)

            blocks.append({"raw": raw_strings, "is_dummy": False})

    return blocks


def build_block(raw_strings):
    count = len(raw_strings)
    offsets = []
    current_offset = 8 + (count * 4)
    for b in raw_strings:
        offsets.append(current_offset)
        current_offset += len(b)

    block_data = bytearray()
    block_data += struct.pack("<I", current_offset)
    block_data += struct.pack("<I", count)
    for off in offsets:
        block_data += struct.pack("<I", off)
    for b in raw_strings:
        block_data += b
    return block_data


def build_papa(blocks_raw):
    blocks = [build_block(b) for b in blocks_raw]
    dummy_block = b"\x00" * 8
    blocks.append(dummy_block)

    lensubfile = len(blocks)
    header_size = (lensubfile * 4) + 0x08
    first_block_offset = (lensubfile * 4) + 0x14

    block_offsets = [first_block_offset]
    for i in range(lensubfile - 1):
        block_offsets.append(block_offsets[i] + len(blocks[i]))

    papa_data = bytearray()
    papa_data += b"PAPA"
    papa_data += b"\x00\x00\x00\x00"
    papa_data += b"\x0C\x00\x00\x00"
    papa_data += struct.pack("<I", header_size)
    papa_data += struct.pack("<I", lensubfile)
    for off in block_offsets:
        papa_data += struct.pack("<I", off)
    for blk in blocks:
        papa_data += blk
    return papa_data


def get_translation_text(entry):
    translation = (entry.get("translation") or "").strip()
    if translation:
        return translation
    message = entry.get("message") or ""
    original = entry.get("original") or ""
    if message and message != original:
        return message
    return ""


def find_message_index(entry, raw_strings):
    block_strings = entry.get("block_strings") or []
    for s in block_strings:
        if s.get("is_message"):
            try:
                return int(s.get("index"))
            except Exception:
                pass
    for key in ("message_index", "msg_index", "message_idx"):
        if key in entry:
            try:
                return int(entry[key])
            except Exception:
                pass

    best_idx = None
    best_len = -1
    for i, raw in enumerate(raw_strings):
        enc = check_encoding_raw(raw)
        if enc == "utf-16" and len(raw) > best_len:
            best_len = len(raw)
            best_idx = i
    return best_idx


def get_message_encoding(entry, msg_idx, raw_strings):
    for key in ("message_encoding", "message_enc", "encoding"):
        enc = entry.get(key)
        if enc:
            return enc
    block_strings = entry.get("block_strings") or []
    for s in block_strings:
        try:
            if s.get("is_message") and int(s.get("index", -1)) == msg_idx:
                return s.get("enc") or "utf-16"
        except Exception:
            continue
    if msg_idx is not None and msg_idx < len(raw_strings):
        return check_encoding_raw(raw_strings[msg_idx])
    return "utf-16"


def guess_papa_path(json_path, data, papa_dir):
    source = data.get("source_file")
    if source:
        candidate = os.path.join(papa_dir, source)
        if os.path.exists(candidate):
            return candidate
    base = os.path.splitext(os.path.basename(json_path))[0]
    for ext in (".papa", ".bin"):
        candidate = os.path.join(papa_dir, base + ext)
        if os.path.exists(candidate):
            return candidate
    return None


def apply_translations(blocks, entries, json_name):
    out_blocks = []
    entry_index = 0

    for block in blocks:
        if block.get("is_dummy"):
            continue

        if entry_index >= len(entries):
            print(f"Warning: {json_name} has fewer entries than blocks. Truncating.")
            break

        raw_strings = list(block["raw"])
        entry = entries[entry_index]
        entry_index += 1

        translation = get_translation_text(entry)
        if translation:
            msg_idx = find_message_index(entry, raw_strings)
            if msg_idx is None or msg_idx >= len(raw_strings):
                print(f"Warning: {json_name} entry {entry_index - 1} missing message index.")
            else:
                enc = get_message_encoding(entry, msg_idx, raw_strings)
                try:
                    raw_strings[msg_idx] = encode_text(translation, enc)
                except UnicodeEncodeError:
                    print(
                        f"Warning: Encoding failed for entry {entry_index - 1} using {enc}. "
                        "Fallback to utf-16."
                    )
                    raw_strings[msg_idx] = encode_text(translation, "utf-16")

        out_blocks.append(raw_strings)

    return out_blocks


def fit_papa_to_original_size(papa_data, original_size, json_name):
    current_size = len(papa_data)
    if current_size > original_size:
        overflow = current_size - original_size
        raise ValueError(
            f"{json_name} grew by {overflow} bytes. "
            "Shorten translations in this file, or offset them with shorter "
            "translations in the same file."
        )
    if current_size < original_size:
        # Keep the original XBB layout stable by extending the trailing dummy block.
        papa_data += b"\x00" * (original_size - current_size)
    return papa_data


def read_xbb_template(template_path):
    if not template_path or not os.path.exists(template_path):
        return None
    with open(template_path, "rb") as f:
        if f.read(4) != b"XBB\x01":
            return None
        count = struct.unpack("<I", f.read(4))[0]
        f.seek(0x20)
        entries = []
        for _ in range(count):
            data = f.read(16)
            if len(data) < 16:
                break
            offset, size, unk1, unk2 = struct.unpack("<IIII", data)
            entries.append((offset, size, unk1, unk2))
    return entries


def plan_xbb_layout(papa_files, template_entries, table_offset=0x20):
    table_end = table_offset + (len(papa_files) * 16)
    if not papa_files:
        return [], table_end, False

    if template_entries and len(template_entries) == len(papa_files):
        template_offsets = [offset for offset, _size, _u1, _u2 in template_entries]
        template_sizes = [size for _offset, size, _u1, _u2 in template_entries]
        data_base = max(table_end, min(template_offsets))

        if all(len(papa) == size for papa, size in zip(papa_files, template_sizes)):
            return template_offsets, data_base, True

        offsets = [data_base]
        for i in range(1, len(papa_files)):
            prev_template_end = template_offsets[i - 1] + template_sizes[i - 1]
            gap = template_offsets[i] - prev_template_end
            if gap < 0:
                gap = 0
            offsets.append(offsets[-1] + len(papa_files[i - 1]) + gap)
        return offsets, data_base, False

    offsets = [table_end]
    for papa in papa_files[:-1]:
        offsets.append(offsets[-1] + len(papa))
    return offsets, table_end, False


def repack_xbb(input_dir, output_xbb, papa_dir=None, template_xbb=None):
    files = glob.glob(os.path.join(input_dir, "*.json"))
    files.sort()

    if papa_dir is None:
        parent = os.path.dirname(os.path.abspath(input_dir))
        candidate = os.path.join(parent, "Msg_unpacked")
        papa_dir = candidate if os.path.isdir(candidate) else input_dir

    if template_xbb is None:
        candidate = os.path.join("rom", "ExtractedRomFS", "Msg.xbb")
        if os.path.exists(candidate):
            template_xbb = candidate

    print(f"Repacking {len(files)} files from {input_dir} to {output_xbb}...")

    papa_files = []
    for json_file in files:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        papa_path = guess_papa_path(json_file, data, papa_dir)
        if not papa_path:
            raise FileNotFoundError(f"Missing source PAPA for {json_file}")

        blocks = parse_papa_blocks(papa_path)
        entries = data.get("entries") or []
        blocks_raw = apply_translations(blocks, entries, os.path.basename(json_file))
        papa_data = build_papa(blocks_raw)
        papa_data = fit_papa_to_original_size(
            papa_data, os.path.getsize(papa_path), os.path.basename(json_file)
        )
        papa_files.append(papa_data)

    template_entries = read_xbb_template(template_xbb)
    template_bytes = None
    if template_xbb and os.path.exists(template_xbb):
        with open(template_xbb, "rb") as f:
            template_bytes = f.read()
    table_offset = 0x20
    offsets, data_base, preserve_template_gaps = plan_xbb_layout(
        papa_files, template_entries, table_offset
    )

    xbb_data = bytearray()
    xbb_data += b"XBB\x01"
    xbb_data += struct.pack("<I", len(papa_files))
    xbb_data += b"\x00" * (0x20 - 8)

    entries = []
    for offset, papa in zip(offsets, papa_files):
        entries.append((offset, len(papa)))

    for i, (offset, size) in enumerate(entries):
        if template_entries and i < len(template_entries):
            _toff, _tsize, unk1, unk2 = template_entries[i]
        else:
            unk1 = 0
            unk2 = 0
        xbb_data += struct.pack("<I", offset)
        xbb_data += struct.pack("<I", size)
        xbb_data += struct.pack("<I", unk1)
        xbb_data += struct.pack("<I", unk2)

    if len(xbb_data) < data_base:
        if preserve_template_gaps and template_bytes and len(template_bytes) >= data_base:
            xbb_data += template_bytes[len(xbb_data):data_base]
        else:
            xbb_data += b"\x00" * (data_base - len(xbb_data))

    current_len = len(xbb_data)
    for i, papa in enumerate(papa_files):
        target_offset = entries[i][0]
        if current_len < target_offset:
            if preserve_template_gaps and template_bytes and len(template_bytes) >= target_offset:
                xbb_data += template_bytes[current_len:target_offset]
            else:
                xbb_data += b"\x00" * (target_offset - current_len)
        xbb_data += papa
        current_len = len(xbb_data)

    with open(output_xbb, "wb") as f:
        f.write(xbb_data)

    print(f"Created {output_xbb}")


if __name__ == "__main__":
    import sys

    input_dir = "work/Msg_formatted_json"
    output_xbb = "work/Msg_repacked.xbb"
    papa_dir = None
    template_xbb = None

    if len(sys.argv) > 1:
        input_dir = sys.argv[1]
    if len(sys.argv) > 2:
        output_xbb = sys.argv[2]
    if len(sys.argv) > 3:
        papa_dir = sys.argv[3]
    if len(sys.argv) > 4:
        template_xbb = sys.argv[4]

    repack_xbb(input_dir, output_xbb, papa_dir, template_xbb)
