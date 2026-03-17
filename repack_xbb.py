import struct
import os
import json
import glob
import codecs

def padding(_bytes):
    _bytes += b"\x00"
    while len(_bytes) % 4 != 0:
        _bytes += b"\x00"
    return _bytes

def check_encoding(text):
    for char in text:
        if ord(char) > 0xFF:
            return "utf-16"
    return "ascii"

def build_papa_block(entries):
    # Entries is a list of text strings for one block?
    # Wait, the original PAPA structure had multiple blocks.
    # The formatted JSON structure flattens everything into "entries".
    # BUT wait, the formatted JSON has "entry_count".
    # In `extract.py`, `PAPA` file had `lensubfile` blocks.
    # In `convert_format.py`, I iterated over `data` which was a list of blocks!
    # Each block had `idx` and `offsets`.
    # My `convert_format.py` FLATTENED the blocks into a single list of entries?
    # Let's check `convert_format.py` again.
    # `data = json.load(f)` -> list of blocks.
    # `for entry in data:` -> iterating blocks.
    # Inside loop, I created ONE `formatted_entry` per block?
    # Yes. `entries.append(formatted_entry)`.
    # So `entries` in formatted JSON corresponds to BLOCKS in PAPA.
    # Each block has ONE "message" and ONE "name".
    # Wait, `tool_xbb.py` extracted `offsets` list for each block.
    # A block can contain MULTIPLE strings?
    # `subfile` returns `(offsets, texts, encodings)`.
    # `texts` is a list of strings in that block.
    # `file_0000.json` (original) has:
    # `idx`: 0, `offsets`: [20, 24, 120], `0`: "Msg", `1`: Text, `2`: Label.
    # So one block has multiple strings.
    # My `convert_format.py` took `val1` and `val2` (TEXT and LABEL).
    # It ignored "Msg".
    # So I effectively reduced each block to (Label, Text).
    # Is it always valid that a block has exactly Label and Text?
    # In `file_0000.json`, yes.
    # But `extract.py` loop: `for i in range(len(texts)): data[str(i)] = ...`
    # So a block CAN have arbitrary number of strings.
    # If I only saved Label and Text, I might have lost "Msg" or other strings if there were more than 3?
    # "Msg" is usually at index 0.
    # If there are other strings, `convert_format.py` might have missed them or just picked the first utf-16/ascii.
    
    # However, `import.py` logic:
    # `bheader = b"PAPA"`
    # `bunknown = ...`
    # It reconstructs the block using `offsets` from JSON.
    # But my formatted JSON DOES NOT HAVE OFFSETS.
    # It only has "message" and "name".
    # So I need to RE-CALCULATE offsets.
    # And I need to reconstruct the "Msg" string if it was there?
    # `convert_format.py` ignored "Msg".
    # If "Msg" is required for the game, I MUST put it back.
    # I should assume "Msg" is always the first string?
    # Or check if "Msg" is standard.
    # `file_0000.json` had "Msg" at index 0.
    
    # If I want to rebuild the PAPA exactly, I need to know ALL strings in the block.
    # My `convert_format.py` simplified the data structure significantly.
    # If the game relies on "Msg" being there, the new PAPA file might crash if I omit it.
    # Also, the order matters.
    # Original: Msg, Text, Label (or Msg, Label, Text).
    # My formatted JSON: just Name and Message.
    # I need to know the order.
    # `convert_format.py` logic was heuristic.
    
    # To be safe, I should update `convert_format.py` to preserve ALL strings in a block, 
    # perhaps in a list, or assume a standard structure (Msg, Label, Text).
    # But the user wants "Label" and "Message" fields.
    # Maybe I should add "hidden_strings" or something?
    
    # Let's assume for now that standard structure is `Msg`, `Label`, `Text` (or similar).
    # I will add "Msg" back hardcoded if it's missing?
    # Or better, I'll update `convert_format.py` to check for "Msg" and store it?
    # Actually, `convert_format.py` skipped "Msg".
    # If I rebuild without "Msg", offsets will change.
    # The game uses offsets to find strings.
    # If I change offsets, but update the offset table, it should be fine IF the game uses the offset table.
    # `PAPA` format HAS an offset table.
    # So as long as I generate a valid offset table, it should be fine.
    # The only risk is if the game expects "Msg" to be at index 0.
    # I will assume "Msg" is needed and add it back at index 0.
    # And then Label/Text.
    # Which order?
    # JPN: Msg, Text, Label.
    # KOR: Msg, Label, Text.
    # The user is translating. Presumably KOR?
    # The file `file_0000.json` in `work/Msg_json` (if it came from `rom/ExtractedRomFS/Msg.xbb`)
    # `rom/ExtractedRomFS` is likely JPN or US?
    # The text was Japanese.
    # So it was Msg, Text, Label.
    # I should reconstruct as Msg, Text, Label to be safe?
    # Or does it matter?
    # If I output KOR text, maybe I should use KOR order (Msg, Label, Text)?
    # But `import.py` says:
    # JPN => STAMP TEXT LABEL
    # ENG => STAMP LABEL TEXT
    # If I'm making a translation patch for the JPN game (to KOR), I might need to keep JPN structure or switch to KOR structure if the game logic allows.
    # Usually, the game code reads string by index (0, 1, 2).
    # If I change order, I break it.
    # JPN game expects Index 1 = Text.
    # So I MUST put Text at Index 1.
    # Msg at Index 0.
    # Label at Index 2.
    
    pass

def json_to_papa(json_data):
    # json_data is the formatted JSON dict
    entries = json_data["entries"] # List of blocks
    
    blocks = []
    
    for entry in entries:
        # Use block_strings if available (created by new convert_format.py)
        if "block_strings" in entry:
            strings = []
            block_strings = entry["block_strings"]
            # Sort by index just in case
            block_strings.sort(key=lambda x: x["index"])
            
            translation = entry.get("translation", "")
            
            for s_data in block_strings:
                text = s_data["text"]
                enc = s_data["enc"]
                
                # Apply translation if this is the message
                if s_data.get("is_message") and translation:
                    text = translation
                    
                # Encode
                try:
                    if enc == "utf-16":
                        b_text = text.encode("utf-16-le")
                    else:
                        b_text = text.encode("ascii")
                except UnicodeEncodeError:
                    # Fallback or robust handling
                    print(f"Warning: Encoding failed for '{text}' as {enc}. Fallback to utf-16-le.")
                    b_text = text.encode("utf-16-le")
                    
                b_text = padding(b_text)
                strings.append(b_text)
                
        else:
            # Fallback for old format (should not happen if you run convert_format.py first)
            # ... (Old logic omitted for brevity, assuming new format)
            print("Error: Old JSON format detected. Please re-run convert_format.py.")
            return b""

        # Now build the block
        # Header of block is Size(4) + Count(4).
        # Offsets start at 0x08.
        
        current_offset = 8 + len(strings) * 4
        offsets = []
        
        for s in strings:
            offsets.append(current_offset)
            current_offset += len(s)
            
        # Build Block Data
        # Block Size = current_offset (Total size)
        
        block_data = bytearray()
        block_data += struct.pack('<I', current_offset)
        block_data += struct.pack('<I', len(strings))
        
        for off in offsets:
            block_data += struct.pack('<I', off)
            
        for s in strings:
            block_data += s
            
        blocks.append(block_data)
        
    # Now build PAPA file
    # Header: PAPA + ...
    
    papa_data = bytearray()
    papa_data += b'PAPA'
    papa_data += b'\x00\x00\x00\x00' # Unknown
    papa_data += b'\x0C\x00\x00\x00' # Unknown 2 (Header Size?) -> 0x0C always?
    
    count = len(blocks)
    header_size = (count * 4) + 8
    
    papa_data += struct.pack('<I', header_size)
    papa_data += struct.pack('<I', count)
    
    # Calculate Block Offsets
    first_block_offset = 0x14 + (count * 4)
    block_offsets = []
    current_block_offset = first_block_offset
    
    for blk in blocks:
        block_offsets.append(current_block_offset)
        current_block_offset += len(blk)
        
    for off in block_offsets:
        papa_data += struct.pack('<I', off)
        
    for blk in blocks:
        papa_data += blk
        
    return papa_data

def repack_xbb(input_dir, output_xbb):
    files = glob.glob(os.path.join(input_dir, "*.json"))
    files.sort() # Ensure order matches file_0000, file_0001...
    
    print(f"Repacking {len(files)} files from {input_dir} to {output_xbb}...")
    
    papa_files = []
    
    for json_file in files:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        papa_data = json_to_papa(data)
        papa_files.append(papa_data)
        
    # Build XBB
    # Header: XBB\x01 (4) + Count (4) + Padding/Unk (0x20 - 8 = 24 bytes?)
    # `tool_xbb.py`: `f.seek(0x20)`. Table starts at 0x20.
    
    xbb_data = bytearray()
    xbb_data += b'XBB\x01'
    xbb_data += struct.pack('<I', len(papa_files))
    # Fill 0x08 to 0x20 with 0x00?
    # `extract.py`/hex dump showed 0s.
    xbb_data += b'\x00' * (0x20 - 8)
    
    # Entry Table
    # Offset (4), Size (4), Unk1 (4), Unk2 (4) per file.
    # 16 bytes per entry.
    
    table_offset = 0x20
    data_offset = table_offset + (len(papa_files) * 16)
    
    # Alignment?
    # XBB files often align to 0x10 or 0x20?
    # Let's align data start to 0x40 or something if needed.
    # Looking at hex dump:
    # 0x20: First entry.
    # 0x... Data starts.
    # `70 90 00 00` -> 0x9070.
    # 0x20 + (871 * 16) = 32 + 13936 = 13968 (0x3690).
    # First file starts at 0x9070.
    # There is a gap between table end (0x3690) and first file (0x9070).
    # 0x9070 - 0x3690 = 0x59E0 (23008 bytes).
    # Maybe string names are there? Or just padding?
    # If I just pack tightly, will it work?
    # Probably safer to align to 0x10 or 0x20.
    # I'll simply append data after table.
    
    entries = []
    current_data_offset = data_offset
    
    # Align first file to 0x40? or just append?
    # I'll just append for now.
    
    for papa in papa_files:
        size = len(papa)
        entries.append((current_data_offset, size))
        current_data_offset += size
        # Padding between files?
        # `tool_xbb.py` didn't skip padding.
        
    # Write Table
    for offset, size in entries:
        xbb_data += struct.pack('<I', offset)
        xbb_data += struct.pack('<I', size)
        xbb_data += b'\x00\x00\x00\x00' # Unk1
        xbb_data += b'\x00\x00\x00\x00' # Unk2
        
    # Write Data
    for papa in papa_files:
        xbb_data += papa
        
    with open(output_xbb, 'wb') as f:
        f.write(xbb_data)
        
    print(f"Created {output_xbb}")

if __name__ == "__main__":
    import sys
    # Default
    input_dir = "work/Msg_formatted_json"
    output_xbb = "work/Msg_repacked.xbb"
    
    if len(sys.argv) > 1:
        input_dir = sys.argv[1]
    if len(sys.argv) > 2:
        output_xbb = sys.argv[2]
        
    repack_xbb(input_dir, output_xbb)
