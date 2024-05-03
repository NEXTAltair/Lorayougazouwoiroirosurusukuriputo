import os
import json
import re
from pathlib import Path

response_jsonl = Path(r"H:\lora\素材リスト\スクリプト\batch_NQSlut9MastLSSP5nHvQMkq7_output.jsonl")
output_dir =Path(r"H:\lora\素材リスト\スクリプト\testimg_Processed")

def clean_format(text):
    """
    テキストから無駄な記号を削除
    Args:
        text (str): クリーニングするテキスト。
    Returns:
        str: クリーニング後のテキスト。
    """
    text = re.sub(r'\*\*', '', text)
    text = re.sub(r'\n+', ', ', text)
    text = re.sub(r'\.\s*$', '', text)
    return text

def save_tags_and_captions(response_jsonl, output_dir):
    """
    JSONファイルからタグとキャプションを読み込み、テキストファイルに保存する。

    Args:
        json_file_path (str): 結果が格納されたJSONファイルのパス。
        output_dir (Path): 出力ファイルの保存先ディレクトリ。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(response_jsonl, 'r', encoding='utf-8') as jsonl_file:
        for line in jsonl_file:
            data = json.loads(line.strip())

            # JSONデータからタグとキャプションを抽出して保存
            base_filename = data['custom_id']
            content = data['response']['body']['choices'][0]['message']['content']

            # テキストのフォーマットをクリーンアップ
            content = clean_format(content)

            # 'Tags:' と 'Caption:' のインデックスを見つける
            tags_index = content.find('Tags:')
            caption_index = content.find('Caption:')

            # タグとキャプションのテキストを抽出
            tags_text = content[tags_index + len('Tags:'):caption_index].strip()
            caption_text = content[caption_index + len('Caption:'):].strip()

            # ファイル名の準備
            tags_filename = f"{base_filename}.txt"
            caption_filename = f"{base_filename}.caption"

            # タグをテキストファイルに保存
            with open(os.path.join(output_dir, tags_filename), 'w', encoding='utf-8') as tags_file:
                tags_file.write(tags_text)

            # キャプションをキャプションファイルに保存
            with open(os.path.join(output_dir, caption_filename), 'w', encoding='utf-8') as cap_file:
                cap_file.write(caption_text)

            print(f"Saved tags to {tags_filename}")
            print(f"Saved caption to {caption_filename}")

save_tags_and_captions(response_jsonl, output_dir)