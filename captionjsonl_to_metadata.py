#https://github.com/kohya-ss/sd-scripts/blob/main/finetune/merge_captions_to_metadata.py
#https://github.com/kohya-ss/sd-scripts/blob/main/finetune/merge_dd_tags_to_metadata.py
import json
from cleanup_txt import clean_format
from pathlib import Path

train_images_dir = Path(r'H:\lora\素材リスト\スクリプト\testimg_Processed')
jsonl_file_path = Path(r'H:\lora\素材リスト\スクリプト\batch_NQSlut9MastLSSP5nHvQMkq7_output.jsonl')
output_dir =  Path(r'H:\lora\素材リスト\スクリプト')

def get_full_image_path(directory, filename):
    """# ファイル名とtrain_images_dirを結合してフルパス(image_key)を生成する"""
    for path in Path(directory).rglob(filename + '.webp'): #webp以外は画像処理してないとみなして扱わない
        return str(path)
    return None  # ファイルが見つからなかった場合

def process_jsonl(jsonl_file_path, output_dir):
    metadata = {}
    with open(jsonl_file_path, 'r', encoding='utf-8') as jsonl_file:
        for line in jsonl_file:
            data = json.loads(line.strip())

            #train_images_dirとcustom_idを結合してimage_key作成
            filename = data['custom_id']
            image_key = get_full_image_path(train_images_dir, filename)
            if image_key is None:
                print(f"File not found: {filename}")
                continue  # ファイルが見つからない場合はスキップ
            # JSONデータからタグとキャプションを抽出して保存

            content = data['response']['body']['choices'][0]['message']['content']

            # 応答のフォーマットをクリーンアップ
            content = clean_format(content)

            # 'Tags:' と 'Caption:' が何番目に含まれているかを見つける
            tags_index = content.find('Tags:')
            caption_index = content.find('Caption:')

            # タグとキャプションのテキストを抽出
            tags_text = content[tags_index + len('Tags:'):caption_index].strip()
            caption_text = content[caption_index + len('Caption:'):].strip()

            # メタデータ辞書にキャプションとタグを保存
            metadata[image_key] = {
                    "tags": {"tags": tags_text},
                    "caption": {"caption": caption_text}
                }

        merge_captions = {key: val['caption'] for key, val in metadata.items()}
        merge_tags = {key: val['tags'] for key, val in metadata.items()}
    # キャプションとタグを別々のファイルに保存
    (output_dir / 'metadata_captions.json').write_text(json.dumps(merge_captions, indent=2), encoding="utf-8")
    (output_dir / 'metadata_tags.json').write_text(json.dumps(merge_tags, indent=2), encoding="utf-8")

process_jsonl(jsonl_file_path, output_dir)
