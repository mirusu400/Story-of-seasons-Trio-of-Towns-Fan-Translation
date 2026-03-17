# Story of seasons: Trio of Towns Fan Translation
Harvest Moon - Story of Seasons Trio Of Towns Korean Translate Git

# For anyone who wanted to make fan translation...
I know, there are very seldom people willing to do this gigantic job.

But I just write this documentaion who loved this game very much.

## 1. Make font
You should modify `romfs/Font/mainfont.bffnt` and `romfs/Font/subfont.bffnt`.

Trio of Towns use `bffnt` font, which usually use 3DS games and WiiU Games.

You can extract / build bffnt files using [3dsTool](https://github.com/ObsidianX/3dstools)

On a `/font` directory, I make a example manifest file and sheet PSD (photoshop) files for korean glyphs. You can modify and make your own font.

### 1-1. Setup
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 1-2. Extract / Build BFFNT
Extract a font into a manifest and PNG sheets:
```bash
.venv/bin/python tool_font.py extract rom/ExtractedRomFS/Font/mainfont.bffnt work/mainfont_extract
```

Build a font back from extracted assets:
```bash
.venv/bin/python tool_font.py build font/mainfont_manifest.json work/mainfont.bffnt
```

### 1-3. One-line Korean Main Font Build
Append the bundled Korean donor glyph set to an original `mainfont.bffnt` and rebuild it:
```bash
.venv/bin/python tool_font.py build-from-font rom/ExtractedRomFS/Font/mainfont.bffnt ./KangwonBold.otf work/mainfont_from_ttf.bffnt --chars-file font/font_raw.txt --font-size 14
```

If you also want the merged manifest and PNG sheets for inspection:
```bash
.venv/bin/python tool_font.py build-from-font rom/ExtractedRomFS/Font/mainfont.bffnt ./KangwonBold.otf work/mainfont_from_ttf.bffnt --chars-file font/font_raw.txt --font-size 14 --keep-dir work/mainfont_from_ttf_assets 
```

### 1-4. Render Glyphs Directly From a Font File
Render Hangul directly from a `ttf` / `otf` / `ttc` and append it to a base BFFNT:
```bash
.venv/bin/python tool_font.py build-from-font rom/ExtractedRomFS/Font/mainfont.bffnt /System/Library/Fonts/Supplemental/AppleGothic.ttf work/mainfont_from_ttf.bffnt --chars-file font/font_raw.txt --font-size 14
```

Useful tuning flags:
```bash
.venv/bin/python tool_font.py build-from-font rom/ExtractedRomFS/Font/mainfont.bffnt /path/to/font.ttf work/mainfont_from_ttf.bffnt --chars-file font/font_raw.txt --font-size 14 --x-offset 0 --y-offset 0 --keep-dir work/mainfont_from_ttf_assets
```

If you want to enumerate the font's own cmap instead of using `font_raw.txt`:
```bash
.venv/bin/python tool_font.py build-from-font rom/ExtractedRomFS/Font/mainfont.bffnt /path/to/font.ttc work/mainfont_from_ttf.bffnt --all-font-glyphs --font-index 0
```


## 2. Edit texts
Story of Seasons use custom archiving structure, you should unpack yourself.

### 2-1. Unpack xbb file
On a `romfs/Msg.xbb`, `romfs/DataText.xbb`, there are almost whole texts datas.

But you should unpack, using `Kerameru` which bundled in [Kuriimu](https://github.com/IcySon55/Kuriimu) 

You can extract each xbb and get lots of raw datas, I'll call us `PAPA` files. (Because it has `PAPA` flag at the head of file.)

### 2-2. Convert PAPA file as JSON file
Of course, you can edit `PAPA` files with [Kuriimu](https://github.com/IcySon55/Kuriimu), but I highly recomment use my tools. Kuriimu PAPA plugins are a bit unstable, and maybe not capatable for some PAPA files.

Using `extract.py`, you can convert PAPA as JSON file. Just drag and drop original file/folder and automatically convert as JSON file.

Modify extracted texts, but *YOU SHOULD NOT EDIT ascii ENCODED TEXTS.* if you modify these, games cannot recognize original texts. You only edit *UTF-16* sections.

After modify, use `import.py` and you get `*.out` file. Using `Kerameru` to replace original file, and build or use layeredfs to apply to game.

# Warning
`extract.py` cannot extract whole PAPA files, it can only extract text datas (from Msg.xbb)

## 3. Advanced JSON Format & Repacking
If you want to work with a cleaner JSON format (similar to MSBT structure) and repack it back to `.xbb`, follow these steps:

### 3-1. Prepare Files
1. Place your `Msg.xbb` in `rom/ExtractedRomFS/Msg.xbb` (or adjust scripts).
2. Run `tool_xbb.py` to extract raw data:
   ```bash
   python3 tool_xbb.py rom/ExtractedRomFS/Msg.xbb
   ```
   This creates `rom/ExtractedRomFS/Msg_json`.

### 3-2. Convert to Formatted JSON
Run `convert_format.py` to create the formatted JSON files in `work/Msg_formatted_json`:
```bash
python3 convert_format.py rom/ExtractedRomFS/Msg_json work/Msg_formatted_json
```
The output files will have a structure like:
```json
{
  "source_file": "file_XXXX.papa",
  "entries": [
    { "name": "Label", "message": "Text...", "original": "Text...", "translation": "" }
  ]
}
```

### 3-3. Translate
Edit the `.json` files in `work/Msg_formatted_json`. Put your translation in the `"translation"` field.

### 3-4. Repack to XBB
Run `repack_xbb.py` to build the new `.xbb` file:
```bash
python3 repack_xbb.py work/Msg_formatted_json work/Msg_repacked.xbb
```
You can now use `work/Msg_repacked.xbb` in your game (rename it to `Msg.xbb`).

### Helper Scripts
- `merge_json.py`: Merges Japanese and Korean JSONs (legacy).
