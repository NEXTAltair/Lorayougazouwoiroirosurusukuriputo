from pathlib import Path
from typing import Any,  Optional

from module.log import get_logger
from module.api_utils import APIClientFactory, APIError
from module.cleanup_txt import initialize_tag_cleaner

class ImageAnalyzer:
    """
    画像のキャプション生成、タグ生成などの
    画像分析タスクを実行
    """
    logger = get_logger("ImageAnalyzer")

    def __init__(self):
        self.tag_cleaner = initialize_tag_cleaner()
        self.logger = ImageAnalyzer.logger
        self.format_name = "unknown"

    def initialize(self, api_client_factory: APIClientFactory, models_config: tuple[dict, dict]):
            """
            ImageAnalyzerクラスのコンストラクタ。

            Args:
                api_client_factory (APIClientFactory): API名とAPIクライアントの対応辞書
                models_config (tuple[dict, dict]): (vision_models, score_models) のタプル
            """
            self.api_client_factory = api_client_factory
            self.vision_models, self.score_models = models_config

    @staticmethod
    def get_existing_annotations(image_path: Path) -> Optional[dict[str, list[dict[str, str]]]]:
        """
        画像の参照元ディレクトリから既存のタグとキャプションを取得。

        Args:
            image_path (Path): 画像ファイルのパス

        Returns:
            dict[str, list[dict[str, str]]]: 'tags'と'captions'をキーとする辞書。
            None : 既存のアノテーションが見つからない場合
        例:
        {
            'tags': [
                {'tag': 'nature'},
                {'tag': 'mountain'}
            ],
            'captions': [
                {'caption': 'A beautiful mountain landscape'}
            ]
        }
        """
        existing_annotations = {'tags': [],
                                'captions': []
                                }
        tag_path = image_path.with_suffix('.txt')
        caption_path = image_path.with_suffix('.caption')

        try:
            if tag_path.exists():
                existing_annotations['tags'] = ImageAnalyzer._read_annotations(tag_path, 'tag')
            if caption_path.exists():
                existing_annotations['captions'] = ImageAnalyzer._read_annotations(caption_path, 'caption')

            if not existing_annotations['tags'] and not existing_annotations['captions']:
                ImageAnalyzer.logger.info(f"既存アノテーション無し: {image_path}")
                return None

        except Exception as e:
            ImageAnalyzer.logger.warning(f"アノテーションファイルの読み込み中にエラーが発生しました: {str(e)}")
            return None

        return existing_annotations

    @staticmethod
    def _read_annotations(file_path: Path, key: str) -> list[dict[str, str]]:
        """
        指定されたファイルからアノテーションを読み込み、辞書のリストとして返す。

        Args:
            file_path (Path): 読み込むファイルのパス
            key (str): 辞書のキー ('tag' または 'caption')

        Returns:
            list[dict[str, str]]: アノテーションの辞書リスト
        """
        from module.cleanup_txt import TagCleaner
        with open(file_path, 'r', encoding='utf-8') as f:
            clean_data = TagCleaner.clean_format(f.read())
            items = clean_data.strip().split(',')
            annotations = []
            for item in items:
                stripped_item = item.strip()
                if stripped_item:
                    annotations.append({key: stripped_item})
            return annotations

    def analyze_image(self, image_path: Path, model_id: str, format_name: str="e621") -> dict[str, Any]:
        """
        指定された画像を分析し、結果を返す。

        Args:
            image_path (Path): 分析する画像のファイルパス
            model_id (str): Vision typeのモデルid
            tag_format (str): タグのフォーマット (オプション)

        Returns:
            dict[str, Any]: 分析結果を含む辞書（タグ、キャプション）
        """
        self.format_name = format_name
        try:
            model_name = self.vision_models.get(model_id, {}).get('name')

            api_client, _ = self.api_client_factory.get_api_client(model_name)
            if not api_client:
                raise ValueError(f"'{model_name}' に対応するAPIクライアントが見つかりません。")

            # APIクライアントの generate_caption メソッドを呼び出す
            api_client.set_image_data(image_path)
            tags_str = api_client.generate_caption(image_path, model_name)
            analysis_result = self._process_response(image_path, tags_str, model_id)
            self.logger.debug(f"img: {image_path} model: {model_name} format: {format_name}" )
            return analysis_result
        except APIError as e:
            self.logger.error("API処理中にエラーが発生しました（画像: %s）: %s", image_path, str(e))
            return {'error': str(e), 'image_path': str(image_path)}
        except Exception as e:
            self.logger.error("画像処理中に予期せぬエラーが発生しました（画像: %s）: %s", image_path, str(e))
            return {'error': str(e), 'image_path': str(image_path)}

    def _process_response(self, image_path: Path, tags_str: str ,model_id: int) -> dict[str, Any]:
        """APIレスポンスを処理し、タグ、キャプション、抽出。

        Args:
            image_path (Path): 画像のパス
            tags_str (str): APIからのレスポンス

        Returns:
            dict[str, Any]: タグ、キャプション、画像パスを含む辞書
        """
        try:
            content = self.tag_cleaner.clean_format(tags_str)
            tags_str, caption = self._extract_tags_and_caption(content, str(image_path))

            # タグを分割し、各タグをトリムして空のタグを除外
            tags = [tag.strip() for tag in tags_str.split(',') if tag.strip()]
            return {
                'tags': [{'tag': tag, 'model_id': model_id} for tag in tags],
                'captions': [{'caption': caption, 'model_id': model_id}],
                'image_path': str(image_path)
            }
        except Exception as e:
            self.logger.error("レスポンス処理中にエラーが発生しました（画像: %s）: %s", image_path, str(e))
            raise

    def create_batch_request(self, image_path: Path, model_name: str) -> dict[str, Any]:
        """単一の画像に対するバッチリクエストデータを生成します。

        Args:
            image_path (Path): 処理済み画像のパス
            model_name (str): 使用するモデル名

        Returns:
            dict[str, Any]: バッチリクエスト用のデータ
        """
        api_client, api_provider = self.api_client_factory.get_api_client(model_name)
        if not api_client:
            raise ValueError(f"APIクライアント '{api_provider}' が見つかりません。")

        api_client.set_image_data(image_path)
        api_client.generate_payload(image_path, model_name)
        return api_client.create_batch_request(image_path)

    def _extract_tags_and_caption(self, content: str, image_key: str) -> tuple[str, str]:
        """
        APIレスポンスからタグとキャプションを抽出します。

        Args:
            content (str): APIレスポンスの内容
            image_key (str): 画像のキー（ファイルパス）

        Returns:
            tuple[list[str], str]: 抽出されたタグのリストとキャプション
        """
        tags_index = content.lower().find('tags:')
        caption_index = content.lower().find('caption:')

        if tags_index == -1 and caption_index == -1:
            self.logger.error("画像 %s の処理に失敗しました。タグまたはキャプションが見つかりません。", image_key)
            self.logger.error(" APIからの応答: %s ", content)
            return "", ""

        tags_text = content[tags_index + len('tags:'):caption_index].strip() if tags_index != -1 else ""
        caption_text = content[caption_index + len('caption:'):].strip() if caption_index != -1 else ""

        return self.tag_cleaner.clean_tags(tags_text, self.format_name), self.tag_cleaner.clean_caption(caption_text)

    def _calculate_score(self, tags: list[str], caption: str) -> float:
        """
        タグとキャプションに基づいて画像スコアを計算します。

        Args:
            tags (list[str]): 画像のタグリスト
            caption (str): 画像のキャプション

        Returns:
            float: 計算されたスコア（0.0から1.0の範囲）
        """
        # ここにスコアリングロジックを実装
        # これは簡単な例で、より洗練された方法に置き換えることができます
        tag_score = len(tags) * 0.1  # タグ1つにつき0.1ポイント
        caption_score = len(caption.split()) * 0.05  # キャプションの単語1つにつき0.05ポイント
        return min(tag_score + caption_score, 1.0)  # スコアの上限を1.0に設定 TODO: そのうちやる

    def get_batch_analysis(self, batch_results: dict[str, str], processed_path: Path):
        """
        バッチ処理結果から指定された画像の分析結果を取得します。

        Args:
            batch_results (dict[str, str]): バッチ処理結果 (画像パスをキー、分析結果を値とする辞書)
            processed_path (Path): 処理後の画像のパス

        Returns:
            dict: 画像の分析結果（タグとキャプション）
        """
        # processed_pathから custom_id を取得
        custom_id = processed_path.stem
        content = batch_results.get(custom_id)
        if content:
            return self._process_response(processed_path, content)

# 画像処理のテスト
if __name__ == "__main__":
    from module.api_utils import APIClientFactory
    from module.db import ImageDatabaseManager
    from module.config import get_config
    config = get_config()
    image_path = Path(r'testimg\1_img\file02.png')
    prompt = config['prompts']['main']
    add_prompt = config['prompts']['additional']
    api_keys = config['api']
    idm = ImageDatabaseManager()
    vision, score = idm.get_models()
    # API クライアントファクトリーを作成
    acf = APIClientFactory(api_keys, vision, prompt, add_prompt)
    Ia = ImageAnalyzer()
    Ia.initialize(acf, vision)
    result = Ia.analyze_image(image_path, 'gpt-4o')
    print(f"キャプション: {result['caption']}")
    print(f"タグ: {result['tags']}")
    print(f"スコア: {result['score']}")
