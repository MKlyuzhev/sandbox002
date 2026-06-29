from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "llama3.2:3b"
    ollama_embed_model: str = "nomic-embed-text"

    chroma_persist_dir: str = "./chroma_db"
    chroma_collection: str = "documents"

    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 5

    request_timeout: float = 300.0

    pdf_ocr_enabled: bool = True
    pdf_ocr_dpi: int = 200

    figures_dir: str = "./data/figures"
    pdf_figures_enabled: bool = True
    pdf_figure_text_threshold: int = 200
    pdf_figure_min_size: int = 150
    ollama_vision_model: str = "moondream"
    figure_caption_prompt: str = (
        "Describe this financial chart or figure in detail. Include chart type, "
        "axes, labels, indicators, patterns, trends, and any readable numeric values."
    )


settings = Settings()
