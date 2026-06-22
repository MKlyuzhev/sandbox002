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


settings = Settings()
