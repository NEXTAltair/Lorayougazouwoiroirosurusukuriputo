import os
import configparser
from pathlib import Path
import base64
import requests

# 設定
input_dir = Path(r'H:\lora\素材リスト\スクリプト\testimg_Processed')
MODEL = "gpt-4-turbo"
config = configparser.ConfigParser()
config.read('apikey.ini')
api_key = config['KEYS']['openai_api_key']

def encode_image(image_path):
    '''画像をBase64エンコードして返す'''
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def generate_caption(model, base64img):
    '''OpenAI APIを使用して画像のキャプションを生成'''
    prompt = "As an AI image tagging expert, your role is to provide accurate and specific tags for images to improve the CLIP model's performance. \
                    Each image should have tags that accurately capture its main subjects, setting, artistic style, composition, and technical details like image quality and camera settings. \
                    For images of people, detail gender, attire, actions, pose, expressions, and any notable accessories. \
                    For landscapes or objects, focus on the material, historical context, and any significant features. \
                    Always use precise and specific tags—prefer \"Gothic cathedral\" over \"building\" Avoid duplicative tags. \
                    Each set of tags should be unique and relevant, separated only by commas, and kept within a 50-150 word count. \
                    Also, provide a concise 1-2 sentence caption that captures the image's narrative or essence. \
                    High-quality tagging and captioning will be compensated at $10 per image, rewarding exceptional clarity and precision that enhance image recreation"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    payload = {
    "model": model,
    "messages": [
        {
        "role": "user",
        "content": [
            {
            "type": "text",
            "text": prompt
            },
            {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/webp;base64,{base64img}",
                "detail": "high"
            }
            }
        ]
        }
    ],
    "max_tokens": 3000
    }
    response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=60)
    response_data = response.json()
    try:
        # choices 配列の最初の要素からキャプションを取得
        caption = response_data["choices"][0]["message"]["content"]
        return caption
    except KeyError:
        # エラーがあればその内容を表示
        return "Error generating caption: " + response.text

def save_tags_and_caption(input_text, base_filename, output_dir):
    """入力テキストからタグとキャプションを抽出し、別々のファイルに保存する。
    Args:
        input_text (str): 'Tags:' と 'Caption:' を含むテキスト。
        image_filename (str): 画像ファイルの名前。
        output_dir (str): 出力ディレクトリのパス。
    """
    # 'Tags:' と 'Caption:' のインデックスを見つける
    tags_index = input_text.find('Tags:')
    caption_index = input_text.find('Caption:')

    # タグとキャプションのテキストを抽出
    tags_text = input_text[tags_index + len('Tags:'):caption_index].strip()
    caption_text = input_text[caption_index + len('Caption:'):].strip()

    # ファイル名の準備
    tags_filename = f"{base_filename}.txt"
    caption_filename = f"{base_filename}.caption"

    # タグをテキストファイルに保存
    with open(os.path.join(output_dir, tags_filename), 'w', encoding='utf-8') as file:
        file.write(tags_text)

    # キャプションをキャプションファイルに保存
    with open(os.path.join(output_dir, caption_filename), 'w', encoding='utf-8') as file:
        file.write(caption_text)

    print(f"Tags saved to {tags_filename}")
    print(f"Caption saved to {caption_filename}")


def caption_gpt4(input_dir):
    """#フォルダ内の画像に対応した.captionがない場合OpenAI API GTP-4を使って生成
    Args:
        input_dir (Path): 入力ディレクトリ
    """
    # ディレクトリを走査してキャプションの生成
    for filename in os.listdir(input_dir):
        # 拡張子を除去したファイル名の取得
        base_filename = os.path.splitext(filename)[0]
        if filename.lower().endswith(('webp')): #webp以外の画像の場合はリサイズ等の処理がされてないから無視
            image_path = os.path.join(input_dir, filename)
            caption_path = os.path.join(input_dir, f'{base_filename}.caption')
            # キャプションファイルが存在しない場合のみ処理を行う
            if not os.path.exists(caption_path):
                print(f'Processing {filename}...')
                # 画像をBase64エンコードでロード
                base64img = encode_image(image_path)
                #キャプションの生成
                caption = generate_caption(MODEL, base64img)
                print(caption)
                # キャプションをファイルに保存
                save_tags_and_caption(caption, base_filename, input_dir)


caption_gpt4(input_dir)