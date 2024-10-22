import traceback
from pathlib import Path
import json
import logging
from typing import Any, Optional
from abc import ABC, abstractmethod
import google.generativeai as genai
import anthropic
import requests
import base64
import time

from module.log import get_logger

class APIError(Exception):
    def __init__(self, message: str, api_provider: str = "", error_code: str = "",
                 status_code: int = 0, response: Optional[requests.Response] = None):
        super().__init__(message)
        self.api_provider = api_provider
        self.error_code = error_code
        self.status_code = status_code
        self.response = response

    def __str__(self):
        parts = [f"{self.api_provider}API Error: {self.args[0]}"]
        if self.error_code:
            parts.append(f"Code: {self.error_code}")
        if self.status_code:
            parts.append(f"Status: {self.status_code}")
        return " | ".join(parts)

    @classmethod
    def check_response(cls, response: requests.Response, api_provider: str):
        if response.status_code == 200:
            return

        error_mapping = {
            400: "リクエストの形式または内容に問題がありました",
            401: "APIキー認証エラー",
            403: "API キーには指定されたリソースを使用する権限がありません",
            404: "要求されたリソースが見つかりません",
            413: "リクエストが最大許容バイト数を超えています",
            429: "リクエスト制限に達しました",
            500: "サーバーエラーが発生しました",
            503: "サービスは一時的に利用できません"
        }

        try:
            error_data = response.json().get('error', {})
        except ValueError:
            error_data = {}

        error_message = error_mapping.get(response.status_code, "予期しないエラーが発生しました")
        error_code = error_data.get('code', '')
        detailed_message = error_data.get('message', error_message)

        # Anthropic APIのクレジット不足エラーを特別に処理
        if api_provider.lower() == "claude" and "credit balance is too low" in detailed_message.lower():
            error_message = "クレジット残高が不足しています"
            detailed_message = "Claude APIにアクセスするためのクレジット残高が不足しています。Plans & Billingでアップグレードまたはクレジットを購入してください。"

        raise cls(
            message=f"{error_message}: {detailed_message}",
            api_provider=api_provider,
            error_code=error_code,
            status_code=response.status_code,
            response=response
        )

    @classmethod
    def from_anthropic_error(cls, e: anthropic.APIError, api_provider: str):
        status_code = getattr(e, 'status_code', 400)
        error_code = getattr(e, 'error_code', '')
        error_message = str(e)

        # AnthropicのAPIErrorを擬似的なResponseオブジェクトに変換
        pseudo_response = type('PseudoResponse', (), {
            'status_code': status_code,
            'json': lambda: {'error': {'message': error_message, 'code': error_code}}
        })()

        return cls.check_response(pseudo_response, api_provider)

    def retry_after(self) -> Optional[int]:
        if self.status_code == 429 and self.response is not None:
            return int(self.response.headers.get('Retry-After', 0))
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": str(self.args[0]),
            "api_provider": self.api_provider,
            "error_code": self.error_code,
            "status_code": self.status_code
        }

class APIInterface(ABC):
    @abstractmethod
    def generate_caption(self, image_path: Path) -> str:
        """画像のキャプションとタグを生成。

        Args:
            image_path (Path): 画像のパス。

        Returns:
            str: 生成されたタグとキャプション。
        """
        pass

    @abstractmethod
    def start_batch_processing(self, image_paths: list[Path], options: Optional[dict[str, Any]] = None) -> str:
        """バッチ処理を開始

        Args:
            image_paths (list[Path]): 画像のパスリスト。
            options (Optional[dict[str, Any]]): API固有のオプション (例: Gemini の `gcs_output_uri`)。

        Returns:
            str: バッチ処理のIDやステータスなどを表す文字列。
        """
        pass

    @abstractmethod
    def get_batch_results(self, batch_result: Any) -> list[dict[str, Any]]:
        """バッチ処理の結果を取得します。

        Args:
            batch_result (Any): start_batch_processing メソッドから返されたバッチ処理結果オブジェクト。

        Returns:
            list[dict[str, Any]]: 各画像の分析結果をJSON形式で格納したリスト。
        """
        pass

    @abstractmethod
    def set_image_data(self, image_path: Path) -> None:
        """
        image_dataにパスとバイナリのdictを保存する。

        Args:
            image_path (Path): 画像ファイルのパス

        Raises:
            FileNotFoundError: 指定されたパスに画像ファイルが存在しない場合
            IOError: 画像ファイルの読み込み中にエラーが発生した場合
        """
        pass

class BaseAPIClient(APIInterface):
    """全ての API クライアントに共通する処理を実装したベースクラス。"""
    def __init__(self, prompt: str, add_prompt: str):
        self.prompt = prompt
        self.add_prompt = add_prompt
        self.image_data: dict[str, bytes] = {}  # image_data を空の辞書で初期化:
        self.logger = get_logger("BaseAPIClient")
        self.last_request_time = 0
        self.min_request_interval = 1.0  # 1秒間隔でリクエストを制限

    def _wait_for_rate_limit(self):
        elapsed_time = time.time() - self.last_request_time
        if elapsed_time < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed_time)

    def _request(self, method: str, url: str, headers: dict[str, str], data: Optional[dict[str, Any]] = None) -> str:
        self._wait_for_rate_limit()
        try:
            response = requests.request(method, url, headers=headers, json=data, timeout=60)
            self.last_request_time = time.time()
            APIError.check_response(response, self.__class__.__name__)
            return response.text
        except requests.exceptions.RequestException as e:
            raise APIError(f"リクエスト中にエラーが発生しました: {str(e)}", self.__class__.__name__)


    def set_image_data(self, image_path: Path) -> None:
        """
        画像データを読み込み、パスとバイナリデータの辞書に保存

        Args:
            image_path (Path): 画像ファイルのパス

        Raises:
            FileNotFoundError: 指定されたパスに画像ファイルが存在しない場合
            IOError: 画像ファイルの読み込み中にエラーが発生した場合
        """
        try:
            with open(image_path, "rb") as image_file:
                self.image_data[str(image_path)] = image_file.read()
            self.logger.debug(f"画像データを正常に設定しました: {image_path}")
        except FileNotFoundError:
            self.logger.error(f"画像ファイルが見つかりません: {image_path}")
            raise
        except IOError as e:
            self.logger.error(f"画像ファイルの読み込み中にエラーが発生しました: {e}")
            raise
        except Exception as e:
            self.logger.error(f"予期せぬエラーが発生しました: {e}")
            raise

class OpenAI(BaseAPIClient):
    SUPPORTED_VISION_MODELS = ["gpt-4-turbo", "gpt-4o", "gpt-4o-mini"]
    def __init__(self, api_key: str, prompt: str, add_prompt: str):
        self.logger = get_logger("OpenAI Claude")
        super().__init__(prompt, add_prompt)
        self.model_name = None
        self.openai_api_key = api_key

    def _generate_payload(self, image_path: Path, model_name: str, prompt: str):
        """
        OpenAI APIに送信するペイロードを生成する。

        Args:
            image_path (Path): 画像ファイルのパス
            model_name (str): モデル名
            prompt (str): プロンプト

        Returns:
            dict: APIに送信するペイロード
        """
        if self.image_data is None:
            raise ValueError("画像データが設定されていません。")

        base64_image = base64.b64encode(self.image_data[str(image_path)]).decode('utf-8')
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.openai_api_key}"
        }
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/webp;base64,{base64_image}",
                            "detail": "high"
                        }}
                    ]
                }
            ],
            "max_tokens": 3000
        }
        return headers, payload

    def _analyze_single_image(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """
        単一画像の分析リクエストを送信

        Args:
            payload (dict[str, Any]): APIに送信するペイロード
            headers (dict[str, str]): リクエストヘッダー

        Returns:
            dict[str, Any]: APIからのレスポンス
        """
        self._wait_for_rate_limit()
        try:
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=60
            )
            self.last_request_time = time.time()
            APIError.check_response(response, self.__class__.__name__)
            return response.json()
        except requests.exceptions.Timeout:
            raise APIError("リクエストがタイムアウトしました。後でもう一度お試しください。")
        except requests.exceptions.ConnectionError:
            raise APIError("ネットワーク接続エラーが発生しました。インターネット接続を確認してください。")
        except requests.exceptions.RequestException as e:
            raise APIError(f"リクエスト中にエラーが発生しました: {str(e)}")
        except json.JSONDecodeError:
            raise APIError("APIレスポンスの解析に失敗しました。レスポンスが不正な形式である可能性があります。")

    def generate_caption(self, image_path: Path, model_name: str) -> str:
        """
        画像のキャプションを生成

        Args:
            image_path (Path): キャプションを生成する画像のパス。
            model_name (str): モデル名デフォルトは"gpt-4o"。
            prompt (str): プロンプト

        Returns:
            str: 生成されたキャプション。
        """
        # プロンプトを作成
        headers, payload = self._generate_payload(image_path, model_name, self.prompt)
        # OpenAI APIにプロンプトを送信して応答を生成
        response = self._analyze_single_image(payload, headers)
        # 応答からキャプションを抽出
        content = response['choices'][0]['message']['content']
        return content

    def create_batch_request(self, image_path: Path, model_name: str = "gpt-4o", prompt: str = "") -> dict[str, Any]:
        """
        OpenAI APIに送信するバッチ処理用のペイロードを生成する。
        OpenAI API のバッチ処理で使用する JSONL ファイルの各行に記述する JSON データを生成

        Args:
            image_path (Path): 画像ファイルのパス
            model_name (str): モデル名, デフォルトは"gpt-4o"。
            prompt (str): プロンプト

        Returns:
            dict[str, Any]: バッチリクエスト用のデータ
        """
        if model_name not in self.SUPPORTED_VISION_MODELS:
            raise ValueError(f"そのModelには非対応: {model_name}. Supported models: {', '.join(self.SUPPORTED_VISION_MODELS)}")

        _, payload = self._generate_payload(image_path, model_name, prompt)
        bach_payload = {
            "custom_id": image_path.stem,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": payload
        }
        return bach_payload

    def start_batch_processing(self, jsonl_path: Path) -> str:
        """
        JSONLファイルをアップロードしてバッチ処理を開始する。

        Args:
            jsonl_path (Path): アップロードするJSONLファイルのパス

        Returns:
            str: バッチ処理のID
        """
        url = "https://api.openai.com/v1/files"
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}"
        }
        files = {
            'file': open(jsonl_path, 'rb')
        }
        data = {
            'purpose': 'batch'
        }
        try:
            response = requests.post(url, headers=headers, files=files, data=data, timeout=500)
            APIError.check_response(response, self.__class__.__name__)
            file_id = response.json().get('id')

            if file_id:
                # バッチ処理を開始
                url = "https://api.openai.com/v1/batches"
                headers = {
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json"
                }
                data = {
                    "input_file_id": file_id,
                    "endpoint": "/v1/chat/completions",
                    "completion_window": "24h"
                }
                response = requests.post(url, headers=headers, json=data, timeout=30)
                start_response = response.json()
                self.logger.info(f"バッチ処理が開始されました。 ID: {start_response['id']}")
                return start_response['id']
        except requests.exceptions.Timeout:
            raise APIError("リクエストがタイムアウトしました。後でもう一度お試しください。")
        except requests.exceptions.ConnectionError:
            raise APIError("ネットワーク接続エラーが発生しました。インターネット接続を確認してください。")
        except requests.exceptions.RequestException as e:
            raise APIError(f"リクエスト中にエラーが発生しました: {str(e)}")
        except json.JSONDecodeError:
            raise APIError("APIレスポンスの解析に失敗しました。レスポンスが不正な形式である可能性があります。")
        return ''

    def get_batch_results(self, batch_result_dir: Path) -> dict[str, str]:
        """
        OpenAI API のバッチ処理結果を読み込み、解析します。

        Args:
            batch_result_dir (Path): バッチ結果ファイルが格納されているディレクトリのパス。

        Returns:
            dict[str, str]: 画像パスをキー、分析結果を値とする辞書。
        """
        results = {}
        for jsonl_file in batch_result_dir.glob('*.jsonl'):
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    data = json.loads(line)
                    if 'custom_id' in data and 'response' in data and 'body' in data['response']:
                        custom_id = data['custom_id']
                        content = data['response']['body']['choices'][0]['message']['content']
                        results[custom_id] = content
        return results

class Google(BaseAPIClient):
    SUPPORTED_VISION_MODELS = ["gemini-1.5-pro-exp-0801", "gemini-1.5-pro-preview-0409", "gemini-1.0-pro-vision"]
    def __init__(self, api_key: str, prompt: str, add_prompt: str):
        """
        Google AI Studioとのインターフェースを作成

        Args:
            api_key (str): Google AI StudioのAPIキー。
        """
        self.logger = get_logger("Google Claude")
        super().__init__(prompt, add_prompt)
        self.google_api_key = api_key
        self.model_name = None
        # Set up the model
        generation_config: dict = {
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 64,
        "max_output_tokens": 8192,
        }

        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            model_name="gemini-1.5-pro-latest",
            generation_config=generation_config, # type: ignore
            safety_settings=safety_settings
        )

    def generate_caption(self, image_path: Path, prompt: str, add_prompt: str) -> str:
        """
        画像のキャプションを生成

        Args:
            image_path (Path): キャプションを生成する画像のパス。
            prompt (str): プロンプト
            add_prompt (str): 追加のプロンプト

        Returns:
            str: 生成されたキャプション。
        """
        prompt_parts = self.generate_prompt_parts(image_path, self.prompt, self.add_prompt)
        response = self.model.generate_content(prompt_parts)
        response_str =response.text
        return response_str

    def generate_prompt_parts(self, image_path: Path, prompt: str, add_prompt: str) -> list:
        """
        Google AI Studioに送信するプロンプトを作成

        Args:
            image_path (str): アップロードされる画像のパス。

        Returns:
            list: プロンプトの各部分をリストで返
        """
        image = self.image_data[str(image_path)]

        prompt_parts = [
            f"{prompt}",
            "image/webp: ",
            "",
            "formatJSON: {\"tags\": \"Tag1, Tag2, Tag3\",  \"caption\": \"This is the caption.\"}",
            "ADDITIONAL_PROMPT: Korean girl in a comic book.",
            "TagsANDCaption: {\"tags\": \"dress, long hair, text, sitting, black hair, blue eyes, heterochromia, flower, high heels, two-tone hair, armpits, elbow gloves, red eyes, boots, gloves, white hair, hat flower, nail polish, panties, red rose, pantyshot, purple nails, rose petals, looking at viewer, hat, navel, bare shoulders, choker, petals, red flower, cross-laced clothes, underwear, split-color hair, brown hair\", \"caption\": \"A stylish Korean girl, with heterochromia and a confident gaze, poses amidst scattered roses against a graffiti-marked wall, rendered in a vibrant comic book art style.\" }",
            "image/webp: ",
            "",
            "formatJSON: {\"tags\": \"Tag1, Tag2, Tag3\", \"caption\": \"This is the caption.\"}",
            "ADDITIONAL_PROMPT: japanese idol",
            "TagsANDCaption: {\"tags\": \"1girl, solo, long hair, brown hair, brown eyes, short sleeves, school uniform, white shirt, upper body, collared shirt, hair bobbles, blue bowtie, realistic, japanese, finger frame\", \"caption\": \"A young Japanese idol in a classic school uniform strikes a pose while performing, her energy and focus evident in her expression and hand gestures.\" }",
            "image/webp: ",
            "",
            "formatJSON: {\"tags\": \"Tag1, Tag2, Tag3\", \"caption\": \"This is the caption.\"}",
            "ADDITIONAL_PROMPT: 1boy, tate eboshi, expressionless, fake horns, shoulder armor resembling onigawara with ornamental horns, 3d, full body, a person standing in a circle with their arms spread out., bridge, horizon, lake, mountain, ocean, planet, river, scenery, shore, snow, water, waterfall, solo, weapon, male focus, ornamental horn, white japanese armor, glowing, letterboxed, pillar, full armor, column, tree, outstretched arms, no humans, spread arms, animated character, fantasy setting, mysterious armor, ethereal glow, purple hues, virtual environment, crystals, otherworldly, long black hair, artistic filter, video game graphics, surreal atmosphere, front-facing pose, enigmatic expression, soft focus, virtual costume, obscured eyes, shoulder armor, arm bracers, magical ambiance, An animated fantasy character stands enigmatically in a surreal, crystal-laden environment, exuding a mystical presence as light softly radiates from their ethereal armor.",
            "TagsANDCaption: {\"tags\": \"1boy, solo, tate eboshi, expressionless, fake horns, shoulder armor resembling onigawara with ornamental horns, 3d, full body, a person standing in a circle with their arms spread out., bridge, ornamental horn, white japanese armor, glowing, outstretched arms, fantasy setting, mysterious armor, ethereal glow, purple hues, otherworldly, long black hair, video game graphics, soft focus, obscured eyes, shoulder armor\", \"caption\": \"An animated fantasy character stands enigmatically in a surreal, crystal-laden environment, exuding a mystical presence as light softly radiates from their ethereal armor.\" }",
            "image/webp: ",
            f"{image}",
            "formatJSON: {\"tags\": \"Tag1, Tag2, Tag3\",\n \"caption\": \"This is the caption.\"}",
            f"ADDITIONAL_PROMPT: {add_prompt}",
            "TagsANDCaption: ",
            ]

        return prompt_parts

    def start_batch_processing(self, image_paths: list[Path], options: Optional[dict[str, Any]] = None) -> str:
        #
        #  TODO: 後で実装
        text = "Not implemented yet"
        return text

    def get_batch_results(self, batch_result: Any) -> list[dict[str, Any]]:
        #
        #  TODO: 後で実装
        text = "Not implemented yet"
        respons = []
        return respons

class Claude(BaseAPIClient):
    """Claude API を使用するためのクライアントクラス。"""
    SUPPORTED_VISION_MODELS = [
            "claude-3-5-sonnet-20240620",
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307"]
    def __init__(self, api_key: str, prompt: str, add_prompt: str):
        self.logger = get_logger("Claude Client")
        super().__init__(prompt, add_prompt)
        self.model_name = None
        self.client = anthropic.Anthropic(api_key=api_key)

    def generate_caption(self, image_path: Path, model_name: str = "claude-3-5-sonnet-20240620", **kwargs) -> str:
        """
        Claude API を使用して画像のキャプションを生成します。
        Args:
            image_path (Path): 画像のパス。
            model_name (str): 使用する Claude モデル名。デフォルトは "claude-3-5-sonnet-20240620"。
            **kwargs: API 固有のオプション (現時点では使用しません)。
        Returns:
            str: 生成されたキャプションを含む文字列。
        """
        try:
            if self.image_data is None or str(image_path) not in self.image_data:
                raise ValueError("画像データが設定されていません。")

            # 画像を base64 エンコード
            image_base64 = base64.b64encode(self.image_data[str(image_path)]).decode('utf-8')

            # プロンプトと画像データを含むメッセージを作成
            response = self.client.messages.create(
                model=model_name,
                max_tokens=300,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/webp",
                                    "data": image_base64,
                                },
                            },
                            {
                                "type": "text",
                                "text": self.prompt
                            }
                        ],
                    }
                ],
            )

            if hasattr(response, 'content') and isinstance(response.content, list):
                # TextBlock オブジェクトから text 属性を抽出
                text_content = [block.text for block in response.content if hasattr(block, 'text')]
                return ' '.join(text_content)
            elif isinstance(response, dict) and 'content' in response:
                if isinstance(response['content'], list):
                    # 辞書形式の場合、'text' キーから内容を抽出
                    text_content = [item.get('text', '') for item in response['content'] if 'text' in item]
                    return ' '.join(text_content)
                elif isinstance(response['content'], str):
                    return response['content']

            raise ValueError(f"Unexpected response format: {type(response)}")

        except anthropic.APIError as e:
            # Anthropic APIエラーを APIError クラスに変換
            raise APIError.from_anthropic_error(e, "Claude")
        except Exception as e:
            self.logger.error(f"予期せぬエラーが発生しました: {str(e)}")
            raise APIError(str(e), "Claude")


    def start_batch_processing(self, image_paths: list[Path], options: Optional[dict[str, Any]] = None) -> str:
        """Claude API はバッチ処理をサポートしていません。"""
        raise NotImplementedError("Claude API はバッチ処理をサポートしていません。")

    def get_batch_results(self, batch_id: str) -> list[dict[str, Any]]:
        """Claude API はバッチ処理をサポートしていません。"""
        raise NotImplementedError("Claude API はバッチ処理をサポートしていません。")

class APIClientFactory:
    def __init__(self, api_keys: dict[str, str]):
        self.logger = get_logger("APIClientFactory")
        self.api_clients = None
        self.api_keys = api_keys
        self.logger.debug("初期化")

    def initialize(self, main_prompt: str, add_prompt: str):
        self.api_clients = {}
        self.main_prompt = main_prompt
        self.add_prompt = add_prompt
        if self.api_keys.get("openai_key"):
            if self._validate_openai_key(self.api_keys["openai_key"]):
                self.api_clients["openai"] = OpenAI(
                    api_key=self.api_keys["openai_key"],
                    prompt=self.main_prompt,
                    add_prompt=self.add_prompt
                )
            else:
                self.logger.error("Invalid OpenAI API key")

        if self.api_keys.get("google_key"):
            if self._validate_google_key(self.api_keys["google_key"]):
                self.api_clients["google"] = Google(
                    api_key=self.api_keys["google_key"],
                    prompt=self.main_prompt,
                    add_prompt=self.add_prompt
                )
            else:
                self.logger.error("Invalid Google API key")

        if self.api_keys.get("claude_key"):
            if self._validate_claude_key(self.api_keys["claude_key"]):
                self.api_clients["claude"] = Claude(
                    api_key=self.api_keys["claude_key"],
                    prompt=self.main_prompt,
                    add_prompt=self.add_prompt
                )
            else:
                self.logger.error("Invalid Claude API key")

    def _validate_openai_key(self, api_key: str) -> bool:
        headers = {
            "Authorization": f"Bearer {api_key}"
        }
        response = requests.get("https://api.openai.com/v1/models", headers=headers)
        return response.status_code == 200

    def _validate_google_key(self, api_key: str) -> bool:
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-pro')
            response = model.generate_content("Test")
            return True
        except Exception as e:
            self.logger.error(f"Google API key validation failed: {str(e)}")
            return False

    def _validate_claude_key(self, api_key: str) -> bool:
        client = anthropic.Anthropic(api_key=api_key)
        try:
            client.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=10,
                messages=[
                    {"role": "user", "content": "Hello"}
                ]
            )
            return True
        except anthropic.APIError:
            return False

    def get_api_client(self, model_name: str):
        if model_name in OpenAI.SUPPORTED_VISION_MODELS:
            api_client = self.api_clients.get("openai")
            if api_client:
                api_client.model_name = model_name
            return api_client, "openai"
        if model_name in Google.SUPPORTED_VISION_MODELS:
            api_client = self.api_clients.get("google")
            if api_client:
                api_client.model_name = model_name
            return api_client, "google"
        if model_name in Claude.SUPPORTED_VISION_MODELS:
            api_client = self.api_clients.get("claude")
            if api_client:
                api_client.model_name = model_name
            return api_client, "claude"
        raise ValueError(f"指定されたモデル名に対応する API クライアントが見つかりません: {model_name}")

if __name__ == "__main__":
    # ロギングの設定
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # テスト用のAPIキー（実際の使用時は環境変数や設定ファイルから読み込むべきです）
    api_keys = {
        "openai_key": "your_openai_api_key_here",
        "google_key": "your_google_api_key_here",
        "claude_key": "your_claude_api_key_here"
    }

    # テスト用のプロンプトとモデル設定
    main_prompt = "Describe this image in detail."
    add_prompt = "Focus on the main subject."
    models = [
        {"name": "gpt-4-vision-preview", "provider": "openai", "type": "vision"},
        {"name": "gemini-pro-vision", "provider": "google", "type": "vision"},
        {"name": "claude-3-opus-20240229", "provider": "anthropic", "type": "vision"}
    ]

    try:
        # APIClientFactoryのインスタンス化
        factory = APIClientFactory(api_keys, models, main_prompt, add_prompt)

        # 各APIクライアントの取得とテスト
        for model in models:
            try:
                client, provider = factory.get_api_client(model["name"])
                if client:
                    logger.info(f"Successfully initialized {provider} client for model: {model['name']}")
                    # ここで実際のAPIリクエストをテストすることもできます
                    # 例: result = client.generate_caption(Path("test_image.jpg"))
                    # logger.info(f"Test result: {result}")
                else:
                    logger.warning(f"Failed to initialize client for {provider}")
            except ValueError as e:
                logger.error(f"Error getting API client: {str(e)}")

    except Exception as e:
        logger.error(f"An unexpected error occurred: {str(e)}")
        logger.debug(traceback.format_exc())

    logger.info("API client initialization test completed.")