import struct
import os
import sys
import json

# Ported from extract.py with modifications to remove dependencies
def check_encoding(btext):
    count = 0
    for char in btext:
        # In Python 3, iterating bytes yields integers
        if char == 0x0:
            count += 1
            continue
        if char >= 0x30 and char <= 0x7E:
            continue
        else:
            return "utf-16"
    if count >= 5:
        return "utf-16"
    return "ascii"

def alt_read(f):
    current_pos = f.tell()
    buffer = b""
    offset = 0
    while True:
        f.seek(current_pos + offset)
        tbuf = f.read(2)
        if tbuf == b"\x00\x00" or len(tbuf) < 2:
            break
        else:
            buffer += tbuf[0].to_bytes(1, "little")
            offset += 1

    return buffer

def subfile(f, block, size):
    f.seek(block)
    try:
        blocksize_bytes = f.read(4)
        offsetcount_bytes = f.read(4)
        if len(blocksize_bytes) < 4 or len(offsetcount_bytes) < 4:
            return (0, 0, 0)
            
        blocksize = struct.unpack('<I', blocksize_bytes)[0]
        offsetcount = struct.unpack('<I', offsetcount_bytes)[0]
    except struct.error:
        return (0, 0, 0)

    if offsetcount == 0x00:
        return (0, 0, 0)
    else:
        offsets = []
        sizes = []
        encodings = []
        texts = []
        
        # Read offsets first
        for i in range(offsetcount):
            off_bytes = f.read(4)
            if len(off_bytes) < 4:
                break
            offsets.append(struct.unpack('<I', off_bytes)[0])
            
        if len(offsets) < offsetcount:
            return (0, 0, 0) # Incomplete read

        # Calculate sizes
        for i in range(len(offsets)):
            if i != 0:
                sizes.append(offsets[i] - offsets[i-1])
        sizes.append(size - offsets[-1])

        for (idx, offset) in enumerate(offsets):
            # Exception Handling
            text = b""
            if offset < offsets[0]:
                texts.append("None")
                encodings.append("None")
                continue
            
            f.seek(block + offset)
            try:
                # Method #1. read using size..
                if idx < len(sizes):
                     text = f.read(sizes[idx])
                else:
                     text = alt_read(f)
            except:
                # Method #2. read as you can
                text = alt_read(f)
            
            encoding = check_encoding(text)
            
            try:
                if encoding == "ascii":
                    texts.append(text.decode("ascii"))
                    encodings.append("ascii")
                else:
                    texts.append(text.decode("utf-16"))
                    encodings.append("utf-16")
            except Exception as e:
                # Fallback to repr if decode fails
                texts.append(str(text))
                encodings.append(f"error_{encoding}")
            
        return (offsets, texts, encodings)

def papa_to_json(file, output_json):
    out_json = []
    with open(file, 'rb') as f:
        buf = f.read(4)
        if buf != b"PAPA":
            # print(f"Skipping {file}: Not a PAPA file")
            return
        
        f.seek(0x0c)
        try:
            # Size of header
            headersize = struct.unpack('<I', f.read(4))[0]
            # Count of subfile
            lensubfile = struct.unpack('<I', f.read(4))[0]
        except:
            print(f"Error reading header of {file}")
            return
        
        blockoffsets = []
        for i in range(lensubfile):
            b_off = f.read(4)
            if len(b_off) < 4:
                break
            blockoffsets.append(struct.unpack('<I', b_off)[0])

        filesize = os.path.getsize(file)

        for i in range(len(blockoffsets)):
            if i == len(blockoffsets) - 1:
                size = filesize - blockoffsets[i]
            else:
                size = blockoffsets[i + 1] - blockoffsets[i]
            
            (offsets, texts, encodings) = subfile(f, blockoffsets[i], size)
            
            if texts == 0:
                continue

            data = {
                "idx" : i,
                "offsets": offsets,
            }
            
            for j in range(len(texts)):
                data[str(j)] = texts[j].replace("\u0000","")
                data[f"{j}_enc"] = encodings[j]
            out_json.append(data)

    with open(output_json, 'w', encoding="utf-8") as f:
        f.write(json.dumps(out_json, indent=4, ensure_ascii=False))
    print(f"Converted {os.path.basename(file)} to {os.path.basename(output_json)}")

def unpack_xbb(xbb_path, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    print(f"Unpacking {xbb_path} to {output_dir}...")
    
    with open(xbb_path, 'rb') as f:
        magic = f.read(4)
        if magic != b'XBB\x01':
            print(f"Invalid magic: {magic}")
            return
            
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            print("Failed to read file count")
            return
            
        count = struct.unpack('<I', count_bytes)[0]
        print(f"Found {count} files in XBB archive.")
        
        # Table starts at 0x20
        f.seek(0x20)
        
        entries = []
        for i in range(count):
            entry_data = f.read(16)
            if len(entry_data) < 16:
                break
            offset, size, unk1, unk2 = struct.unpack('<IIII', entry_data)
            entries.append((offset, size))
            
        for i, (offset, size) in enumerate(entries):
            f.seek(offset)
            data = f.read(size)
            
            # Check for PAPA magic
            if len(data) >= 4 and data[:4] == b'PAPA':
                ext = '.papa'
            else:
                ext = '.bin'
                
            filename = f"file_{i:04d}{ext}"
            out_path = os.path.join(output_dir, filename)
            
            with open(out_path, 'wb') as out_f:
                out_f.write(data)
                
    print("Unpacking complete.")

def process_all(xbb_file):
    # 1. Unpack
    base_name = os.path.splitext(os.path.basename(xbb_file))[0]
    dir_name = os.path.dirname(xbb_file)
    unpack_dir = os.path.join(dir_name, base_name + "_unpacked")
    
    unpack_xbb(xbb_file, unpack_dir)
    
    # 2. Convert PAPA to JSON
    json_dir = os.path.join(dir_name, base_name + "_json")
    if not os.path.exists(json_dir):
        os.makedirs(json_dir)
    
    print(f"Converting unpacked files to JSON in {json_dir}...")
    
    for root, dirs, files in os.walk(unpack_dir):
        files.sort()
        for file in files:
            if file.endswith(".papa"):
                papa_path = os.path.join(root, file)
                json_filename = os.path.splitext(file)[0] + ".json"
                json_path = os.path.join(json_dir, json_filename)
                try:
                    papa_to_json(papa_path, json_path)
                except Exception as e:
                    print(f"Failed to convert {file}: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tool_xbb.py <xbb_file>")
        sys.exit(1)
    
    process_all(sys.argv[1])
