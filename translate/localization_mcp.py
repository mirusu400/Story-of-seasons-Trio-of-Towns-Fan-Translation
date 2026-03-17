# localization_mcp.py
from fastmcp import FastMCP
import json
import re
import os

mcp = FastMCP("GameLocalization")
code_map = {}

TM_FILE = "master_translation_memory.json"
translation_memory = {}
script_dir = os.path.dirname(os.path.abspath(__file__))
base_path = os.path.join(script_dir, "..", "work", "Msg_formatted_json")
if os.path.exists(TM_FILE):
    with open(TM_FILE, "r", encoding="utf-8") as f:
        translation_memory = json.load(f)
    print(f"✅ 번역 메모리 로드 완료: {len(translation_memory)}개")


def get_pure_text(text):
    if not text:
        return ""
    # 기존 대괄호 정규식에서 꺾쇠 정규식으로 변경
    return re.sub(r"<.*?>", "", text).strip()


def mask_text(text, file_path, entry_id):
    # < > 로 둘러싸인 제어 코드 찾기
    codes = re.findall(r"<.*?>", text)
    masked = text
    key = f"{file_path}_{entry_id}"
    code_map[key] = codes
    for i, code in enumerate(codes):
        # 원본이 < > 이므로, 마스킹 기호는 충돌 방지를 위해 [0], [1] 형태로 사용
        masked = masked.replace(code, f"[{i}]", 1)
    return masked


def unmask_text(masked_text, file_path, entry_id):
    key = f"{file_path}_{entry_id}"
    if key not in code_map:
        return masked_text
    codes = code_map[key]
    unmasked = masked_text
    for i, code in enumerate(codes):
        # mask_text에서 씌웠던 [0], [1] 형태를 찾아 원본 제어 코드로 롤백
        unmasked = unmasked.replace(f"[{i}]", code)
    return unmasked


@mcp.tool()
def get_json_file_list(limit: int = 30) -> str:
    """폴더를 뒤져서 '아직 번역이 안 된' 파일 목록을 가져옵니다."""
    folder_path = base_path
    if not os.path.exists(folder_path):
        return "폴더를 찾을 수 없습니다."

    files = sorted([f for f in os.listdir(folder_path) if f.endswith(".json")])
    pending_files = []

    for filename in files:
        if "manifest" in filename:
            continue
        file_path = os.path.join(folder_path, filename)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            needs_translation = False
            for entry in data.get("entries", []):
                if not entry.get("translation"):
                    if entry.get("message", "").strip():
                        needs_translation = True
                        break

            if needs_translation:
                pending_files.append(filename)

            if len(pending_files) == limit:
                break
        except Exception:
            continue

    if not pending_files:
        return "🎉 모든 파일의 번역이 완료되었습니다!"

    return json.dumps(pending_files, ensure_ascii=False)


@mcp.tool()
def load_and_mask_json(file_name: str, chunk_size: int = 40) -> str:
    """[핵심 수정] 파일이 너무 커서 클로드가 터지는 것을 방지. 번역 안 된 것만 최대 40개 잘라서 줌!"""
    file_path = os.path.join(base_path, file_name)
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    slim_entries = []
    untranslated_count = 0

    for entry in data.get("entries", []):
        # 이미 번역된 건 굳이 클로드에게 주지 않고 패스!
        if entry.get("translation"):
            continue

        original_msg = entry.get("message", "")
        # 내용이 아예 없는 시스템 데이터도 패스!
        if not original_msg.strip():
            continue

        untranslated_count += 1

        # 우리가 정한 한도(40개)까지만 담아서 줍니다.
        if len(slim_entries) < chunk_size:
            slim_entry = {
                "name": entry["name"],
                "message": mask_text(original_msg, file_path, entry["name"]),
                "tm_hint": {},
            }

            pure_jp = get_pure_text(original_msg)
            for translation_ori_jp in translation_memory.keys():
                if translation_ori_jp in pure_jp:
                    slim_entry["tm_hint"][translation_ori_jp] = translation_memory[
                        translation_ori_jp
                    ]

            slim_entries.append(slim_entry)

    result = {
        "file": os.path.basename(file_path),
        "total_pending_in_file": untranslated_count,
        "loaded_chunk_size": len(slim_entries),
        "entries": slim_entries,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def save_translated_json(original_file_name: str, translated_json_str: str) -> str:
    """클로드의 번역 결과물 40개를 원본 511개 파일에 안전하게 끼워넣습니다."""
    original_file_path = os.path.join(base_path, original_file_name)
    with open(original_file_path, "r", encoding="utf-8") as f:
        original_data = json.load(f)

    try:
        translated_data = json.loads(translated_json_str)
        claude_entries = (
            translated_data.get("entries", translated_data)
            if isinstance(translated_data, dict)
            else translated_data
        )
    except Exception as e:
        return f"데이터 파싱 에러: {e}"

    translation_map = {}
    for item in claude_entries:
        if isinstance(item, dict) and "name" in item and "translation" in item:
            translation_map[item["name"]] = item["translation"]

    for entry in original_data.get("entries", []):
        name_id = entry["name"]
        # 클로드가 번역해준 40개에 해당하는 ID만 골라서 업데이트!
        if name_id in translation_map:
            entry["translation"] = unmask_text(
                translation_map[name_id], original_file_path, name_id
            )

    with open(original_file_path, "w", encoding="utf-8") as f:
        json.dump(original_data, f, ensure_ascii=False, indent=2)

    return f"원본 손실 없이 부분 병합(Merge) 저장되었습니다: {original_file_path}"


if __name__ == "__main__":
    mcp.run()
