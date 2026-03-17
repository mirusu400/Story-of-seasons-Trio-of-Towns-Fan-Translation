import json
import os
import glob

def convert_to_msbt_format(input_dir, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    files = glob.glob(os.path.join(input_dir, "*.json"))
    print(f"Found {len(files)} files to convert.")
    
    for file_path in files:
        filename = os.path.basename(file_path)
        base_name = os.path.splitext(filename)[0]
        output_filename = base_name + ".json"
        
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                print(f"Error decoding {filename}, skipping.")
                continue
                
        entries = []
        for entry in data:
            # entry is a dict like {"idx": 0, "offsets": [], "0": "Msg", "0_enc": "ascii", ...}
            
            # 1. Collect all strings
            block_strings = []
            
            # Find all numeric keys
            indices = []
            for k in entry.keys():
                if k.isdigit():
                    indices.append(int(k))
            indices.sort()
            
            for idx in indices:
                k = str(idx)
                text = entry.get(k)
                enc = entry.get(f"{k}_enc", "ascii") # Default to ascii if missing
                
                block_strings.append({
                    "index": idx,
                    "text": text,
                    "enc": enc,
                    "is_message": False,
                    "is_label": False
                })
                
            # 2. Identify Message and Label
            # Heuristic:
            # - Label: ASCII, not "Msg"
            # - Message: UTF-16, usually longest
            
            msg_candidates = [s for s in block_strings if s["enc"] == "utf-16"]
            ascii_candidates = [s for s in block_strings if s["enc"] == "ascii" and s["text"] != "Msg"]
            
            main_message = ""
            label = ""
            
            # Identify Message
            if msg_candidates:
                # Pick the longest one as the main message
                longest = max(msg_candidates, key=lambda x: len(x["text"]))
                longest["is_message"] = True
                main_message = longest["text"]
            
            # Identify Label
            if ascii_candidates:
                # Pick the first one?
                # Sometimes there are multiple ASCII strings.
                # Usually Label is the last one or specific index.
                # Let's pick the one that looks most like an ID (no spaces, alphanumeric).
                # For now, just pick the first valid ascii candidate.
                target_label = ascii_candidates[0]
                target_label["is_label"] = True
                label = target_label["text"]
            elif not label and msg_candidates:
                 # If no ascii label, but we have text...
                 pass
                 
            # 3. Create formatted entry
            formatted_entry = {
                "name": label if label else f"Entry_{entry['idx']}",
                "message": main_message,
                "original": main_message,
                "translation": "",
                # Structural data for repacking
                "block_strings": block_strings 
            }
            entries.append(formatted_entry)
            
        output_data = {
            "source_file": f"{base_name}.papa",
            "entry_count": len(entries),
            "entries": entries
        }
        
        output_path = os.path.join(output_dir, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
            
    print(f"Converted {len(files)} files to {output_dir}")

if __name__ == "__main__":
    # Default paths
    input_dir = "work/Msg_json"
    output_dir = "work/Msg_formatted_json"
    
    import sys
    if len(sys.argv) > 1:
        input_dir = sys.argv[1]
    if len(sys.argv) > 2:
        output_dir = sys.argv[2]
        
    convert_to_msbt_format(input_dir, output_dir)
