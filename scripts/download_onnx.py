import os
import shutil
from pathlib import Path
from huggingface_hub import hf_hub_download

repo_id = "onnx-community/gliner_multi-v2.1"
dest_dir = Path(r"path_to_folder_models\models\gliner_multi-v2.1-onnx")
dest_dir.mkdir(parents=True, exist_ok=True)

files_to_download = [
    ("config.json", "config.json"),
    ("gliner_config.json", "gliner_config.json"),
    ("tokenizer.json", "tokenizer.json"),
    ("tokenizer_config.json", "tokenizer_config.json"),
    ("spm.model", "spm.model"),
    ("added_tokens.json", "added_tokens.json"),
    ("special_tokens_map.json", "special_tokens_map.json"),
    ("onnx/model_quantized.onnx", "model_quantized.onnx")
]

for src_name, dest_name in files_to_download:
    print(f"Downloading {src_name}...")
    try:
        cached_path = hf_hub_download(repo_id=repo_id, filename=src_name)
        shutil.copy(cached_path, dest_dir / dest_name)
        print(f"Saved {dest_name} to {dest_dir}")
    except Exception as e:
        print(f"Could not download {src_name}: {e}")

print("Download complete!")
