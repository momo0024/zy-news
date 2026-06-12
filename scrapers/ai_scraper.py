"""
ScrapeGraphAI 智能解析模块
使用 vLLM 本地部署 (OpenAI 兼容接口)
"""

import json
from typing import Optional

from loguru import logger

from config import LLMConfig


class AIScraper:
    """
    ScrapeGraphAI 封装 - 智能网页内容解析

    使用方式:
        scraper = AIScraper(model_name="qwen3.6:27b")
        result = scraper.extract(page_content=html, url="https://...")
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: Optional[float] = None,
    ):
        """
        初始化 AI 解析器

        Args:
            provider: LLM 提供商，默认 openai_compatible
            model_name: 模型名称，不传则使用配置默认值
            api_key: API 密钥 (vLLM 本地部署不需要)
            base_url: API 基础 URL (vLLM 默认 http://localhost:8000/v1)
            temperature: 温度参数
        """
        self.provider = provider or LLMConfig.PROVIDER
        self.model_name = model_name or LLMConfig.OPENAI_MODEL_NAME
        self.api_key = api_key or LLMConfig.OPENAI_API_KEY
        self.base_url = base_url or LLMConfig.OPENAI_BASE_URL
        self.temperature = temperature or LLMConfig.OPENAI_TEMPERATURE
        self._llm = None
        self._configured = False

        logger.info(
            f"AIScraper 初始化 | provider={self.provider} | model={self.model_name}"
        )

    def _get_llm_instance(self):
        """创建 LLM 实例 (ChatOpenAI 兼容接口)"""
        try:
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=self.model_name,
                temperature=self.temperature,
                max_tokens=LLMConfig.OPENAI_MAX_TOKENS,
                # vLLM 本地部署不需要真实 key，传占位值即可
                api_key=self.api_key or "not-needed",
                base_url=self.base_url,
                # Qwen3 思考模型推理深度
                reasoning_effort=LLMConfig.REASONING_EFFORT,
            )
        except ImportError as e:
            logger.error(f"缺少 LLM 依赖: {e}")
            raise

    def _ensure_llm(self):
        """确保 LLM 实例已创建"""
        if self._llm is None:
            self._llm = self._get_llm_instance()
            self._configured = True

    def extract_from_html(
        self,
        html_content: str,
        url: str = "",
        extraction_prompt: Optional[str] = None,
    ) -> dict:
        """
        使用 ScrapeGraphAI 从 HTML 内容中提取结构化数据

        Args:
            html_content: 网页 HTML 内容
            url: 网页 URL (用于日志)
            extraction_prompt: 自定义提取指令，不传则使用配置默认值

        Returns:
            结构化数据字典，格式见 EXTRACTION_PROMPT 定义
        """
        self._ensure_llm()

        prompt = extraction_prompt or LLMConfig.EXTRACTION_PROMPT

        try:
            from scrapegraphai.graphs import SmartScraperGraph

            graph_config = {
                "llm": self._llm,
                "verbose": True,
                "headless": True,
            }

            smart_scraper = SmartScraperGraph(
                prompt=prompt,
                source=html_content,
                config=graph_config,
            )

            logger.info(f"ScrapeGraphAI 开始解析 | url={url[:80]}")
            result = smart_scraper.run()
            logger.success(f"ScrapeGraphAI 解析完成 | url={url[:80]}")

            # 如果返回的是列表，取第一个元素
            if isinstance(result, list) and len(result) > 0:
                result = result[0]
            if not isinstance(result, dict):
                logger.warning(f"ScrapeGraphAI 返回非预期格式: {type(result)}")
                return {}

            return result

        except ImportError as e:
            logger.error(f"ScrapeGraphAI 未安装或依赖缺失: {e}")
            raise
        except Exception as e:
            logger.error(f"ScrapeGraphAI 解析失败: {e}")
            raise

    def extract_from_url(
        self,
        url: str,
        extraction_prompt: Optional[str] = None,
    ) -> dict:
        """
        直接通过 URL 抓取并提取 (ScrapeGraphAI 内置请求)

        Args:
            url: 目标网页 URL
            extraction_prompt: 自定义提取指令

        Returns:
            结构化数据字典
        """
        self._ensure_llm()

        prompt = extraction_prompt or LLMConfig.EXTRACTION_PROMPT

        try:
            from scrapegraphai.graphs import SmartScraperGraph

            graph_config = {
                "llm": self._llm,
                "verbose": True,
                "headless": True,
            }

            smart_scraper = SmartScraperGraph(
                prompt=prompt,
                source=url,
                config=graph_config,
            )

            logger.info(f"ScrapeGraphAI 开始抓取+解析 | url={url[:80]}")
            result = smart_scraper.run()
            logger.success(f"ScrapeGraphAI 完成 | url={url[:80]}")

            if isinstance(result, list) and len(result) > 0:
                result = result[0]
            if not isinstance(result, dict):
                logger.warning(f"ScrapeGraphAI 返回非预期格式: {type(result)}")
                return {}

            return result

        except Exception as e:
            logger.error(f"ScrapeGraphAI URL 解析失败: {e}")
            raise

    def extract_from_search(
        self,
        query: str,
        extraction_prompt: Optional[str] = None,
    ) -> dict:
        """
        使用 ScrapeGraphAI SearchGraph 进行搜索+抓取一体化

        Args:
            query: 搜索查询
            extraction_prompt: 自定义提取指令

        Returns:
            搜索结果结构化数据
        """
        self._ensure_llm()

        prompt = extraction_prompt or LLMConfig.EXTRACTION_PROMPT

        try:
            from scrapegraphai.graphs import SearchGraph

            graph_config = {
                "llm": self._llm,
                "verbose": True,
                "headless": True,
            }

            search_graph = SearchGraph(
                prompt=prompt,
                config=graph_config,
            )

            logger.info(f"ScrapeGraphAI SearchGraph 搜索 | query={query}")
            result = search_graph.run()
            logger.success(f"ScrapeGraphAI SearchGraph 完成 | query={query}")

            return result

        except Exception as e:
            logger.error(f"ScrapeGraphAI SearchGraph 失败: {e}")
            raise

    @classmethod
    def list_available_providers(cls) -> dict:
        """列出支持的 LLM 提供商及配置说明"""
        return {
            "openai": {
                "description": "OpenAI 官方 API (gpt-4o-mini, gpt-4o 等)",
                "env_vars": ["OPENAI_API_KEY", "OPENAI_MODEL_NAME"],
            },
            "openai_compatible": {
                "description": "OpenAI 兼容接口 (通义千问、DeepSeek 等)",
                "env_vars": ["OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL_NAME"],
            },
            "ollama": {
                "description": "Ollama 本地模型 (qwen2.5, llama3 等)",
                "env_vars": ["OLLAMA_BASE_URL", "OLLAMA_MODEL_NAME"],
            },
        }