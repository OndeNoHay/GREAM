"""
Configuración centralizada de la aplicación.
Gestiona variables de entorno y configuración de servicios externos.
"""
from pydantic_settings import BaseSettings
from pydantic import Field, validator
from typing import Optional, List
import os
from pathlib import Path


class Settings(BaseSettings):
    """Configuración general de la aplicación"""
    
    # Aplicación
    APP_NAME: str = "Knowledge Graph PoC"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(default="development", env="ENVIRONMENT")
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    
    # API
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8000"]
    
    # Neo4j
    NEO4J_URI: str = Field(default="bolt://localhost:7687", env="NEO4J_URI")
    NEO4J_USER: str = Field(default="neo4j", env="NEO4J_USER")
    NEO4J_PASSWORD: str = Field(default="password123", env="NEO4J_PASSWORD")
    NEO4J_DATABASE: str = Field(default="neo4j", env="NEO4J_DATABASE")
    
    # Ollama (LLMs locales)
    OLLAMA_BASE_URL: str = Field(default="http://localhost:11434", env="OLLAMA_BASE_URL")
    OLLAMA_DEFAULT_MODEL: str = Field(default="llama3", env="OLLAMA_DEFAULT_MODEL")
    OLLAMA_TIMEOUT: int = 300  # 5 minutos
    
    # Google Gemini
    GEMINI_API_KEY: Optional[str] = Field(default=None, env="GEMINI_API_KEY")
    GEMINI_DEFAULT_MODEL: str = Field(default="gemini-2.0-flash", env="GEMINI_DEFAULT_MODEL")
    
    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379", env="REDIS_URL")
    REDIS_CACHE_TTL: int = 3600  # 1 hora
    
    # ChromaDB (embeddings)
    CHROMA_PERSIST_DIR: str = "./data/chroma"
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    
    # Procesamiento de documentos
    MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB
    ALLOWED_EXTENSIONS: List[str] = [".pdf", ".docx", ".txt", ".xml", ".md"]
    UPLOAD_DIR: str = "./data/uploads"
    PROCESSED_DIR: str = "./data/processed"
    
    # Chunking de texto
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200
    
    # Knowledge Graph
    MAX_ENTITIES_PER_CHUNK: int = 20
    MAX_RELATIONS_PER_CHUNK: int = 30
    ENTITY_TYPES: List[str] = [
        "Component",
        "Procedure",
        "Requirement",
        "Document",
        "Standard",
        "Inspection",
        "Part",
        "System",
        "Directive"
    ]
    RELATION_TYPES: List[str] = [
        "REQUIRES",
        "AFFECTS",
        "REFERENCES",
        "PART_OF",
        "FOLLOWS",
        "CONFLICTS_WITH",
        "SUPERSEDES",
        "APPLIES_TO"
    ]
    
    # Graph RAG
    RAG_TOP_K_VECTORS: int = 5
    RAG_MAX_GRAPH_DEPTH: int = 2
    RAG_CONTEXT_WINDOW: int = 4000
    
    # LLM Configuration
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 2000
    LLM_PROVIDER: str = "ollama"  # "ollama" o "gemini"
    
    @validator("UPLOAD_DIR", "PROCESSED_DIR", "CHROMA_PERSIST_DIR")
    def create_directories(cls, v):
        """Crea los directorios si no existen"""
        Path(v).mkdir(parents=True, exist_ok=True)
        return v
    
    @validator("CORS_ORIGINS", pre=True)
    def parse_cors_origins(cls, v):
        """Parsea CORS_ORIGINS desde string o lista"""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# Instancia global de configuración
settings = Settings()


# Configuración de logging
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "detailed": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": settings.LOG_LEVEL,
            "formatter": "default",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "INFO",
            "formatter": "detailed",
            "filename": "logs/app.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
        },
    },
    "loggers": {
        "app": {
            "level": settings.LOG_LEVEL,
            "handlers": ["console", "file"],
            "propagate": False,
        },
    },
    "root": {
        "level": settings.LOG_LEVEL,
        "handlers": ["console"],
    },
}


# Crear directorio de logs
Path("logs").mkdir(exist_ok=True)
