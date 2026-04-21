"""
backend/app/services/llm_service.py
Servicio unificado para interactuar con LLMs (Ollama y Gemini)
VERSIÓN COMPLETA CON TODOS LOS MÉTODOS
"""

import logging
from typing import Dict, List, Optional, Any
import httpx
from langchain_google_genai import ChatGoogleGenerativeAI

# Import para ChatOllama
try:
    from langchain_ollama import ChatOllama
except ImportError:
    try:
        from langchain_community.chat_models import ChatOllama
    except ImportError:
        ChatOllama = None
        logging.warning("No se pudo importar ChatOllama")

from app.config import settings

logger = logging.getLogger("app.llm_service")


class LLMService:
    """
    Servicio para interactuar con diferentes proveedores de LLM.
    Soporta: Ollama (local) y Google Gemini (cloud)
    """
    
    def __init__(
        self,
        provider: Optional[str] = None,
        ollama_base_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
        gemini_api_key: Optional[str] = None
    ):
        """
        Inicializa el servicio LLM
        
        Args:
            provider: 'ollama' o 'gemini' (default: settings.LLM_PROVIDER)
            ollama_base_url: URL de Ollama (default: settings.OLLAMA_BASE_URL)
            ollama_model: Modelo de Ollama (default: settings.OLLAMA_DEFAULT_MODEL)
            gemini_api_key: API key de Gemini (default: settings.GEMINI_API_KEY)
        """
        self.provider = provider or settings.LLM_PROVIDER
        self.ollama_base_url = ollama_base_url or settings.OLLAMA_BASE_URL
        self.ollama_model = ollama_model or settings.OLLAMA_DEFAULT_MODEL
        self.gemini_api_key = gemini_api_key or settings.GEMINI_API_KEY
        
        # Cliente HTTP para Ollama
        self.http_client = httpx.AsyncClient(timeout=120.0)
        
        # Cliente Gemini (se inicializa si está disponible)
        self.gemini_client = None
        if self.gemini_api_key:
            try:
                self.gemini_client = ChatGoogleGenerativeAI(
                    model="gemini-2.0-flash-exp",
                    google_api_key=self.gemini_api_key,
                    temperature=0.3
                )
            except Exception as e:
                logger.warning(f"No se pudo inicializar Gemini: {e}")
        
        logger.info(f"LLMService inicializado con provider: {self.provider}")
    
    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        system_prompt: Optional[str] = None
    ) -> str:
        """
        Genera texto usando el LLM configurado
        
        Args:
            prompt: Texto del prompt
            max_tokens: Máximo de tokens a generar
            temperature: Temperatura (0.0-1.0)
            system_prompt: System prompt opcional
            
        Returns:
            Texto generado por el LLM
        """
        # Intentar con el provider configurado
        if self.provider == "ollama":
            try:
                return await self._generate_ollama(prompt, max_tokens, temperature, system_prompt)
            except Exception as e:
                logger.warning(f"Ollama falló: {e}, intentando Gemini...")
                if self.gemini_client:
                    return await self._generate_gemini(prompt, max_tokens, temperature, system_prompt)
                raise
        
        elif self.provider == "gemini":
            if not self.gemini_client:
                raise ValueError("Gemini API key no configurada")
            try:
                return await self._generate_gemini(prompt, max_tokens, temperature, system_prompt)
            except Exception as e:
                logger.warning(f"Gemini falló: {e}, intentando Ollama...")
                return await self._generate_ollama(prompt, max_tokens, temperature, system_prompt)
        
        else:
            raise ValueError(f"Provider no soportado: {self.provider}")
    
    async def _generate_ollama(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        system_prompt: Optional[str]
    ) -> str:
        """Genera texto usando Ollama"""
        url = f"{self.ollama_base_url}/api/generate"
        
        # Construir prompt completo
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"
        
        payload = {
            "model": self.ollama_model,
            "prompt": full_prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }
        
        response = await self.http_client.post(url, json=payload)
        response.raise_for_status()
        
        result = response.json()
        return result.get("response", "")
    
    async def _generate_gemini(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        system_prompt: Optional[str]
    ) -> str:
        """Genera texto usando Google Gemini"""
        if not self.gemini_client:
            raise ValueError("Gemini no está configurado")
        
        # Construir prompt completo
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"
        
        # Gemini usa invoke sincrónicamente, así que lo envolvemos
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.gemini_client.invoke(full_prompt)
        )
        
        return result.content

    async def get_llm(self):
        """
        Retorna una instancia de LLM compatible con LangChain.
        Usado por KGBuilder para extracción de entidades/relaciones.

        Returns:
            Instancia de ChatOllama o ChatGoogleGenerativeAI
        """
        if self.provider == "ollama":
            if ChatOllama is None:
                raise ImportError(
                    "ChatOllama no disponible. Instala: pip install langchain-ollama"
                )

            return ChatOllama(
                base_url=self.ollama_base_url,
                model=self.ollama_model,
                temperature=0.1,
            )

        elif self.provider == "gemini":
            if not self.gemini_client:
                raise ValueError("Gemini no está configurado")

            return self.gemini_client

        else:
            # Fallback: intentar Ollama primero
            if ChatOllama is not None and await self._check_ollama():
                logger.warning(f"Provider {self.provider} no válido, usando Ollama")
                return ChatOllama(
                    base_url=self.ollama_base_url,
                    model=self.ollama_model,
                    temperature=0.1,
                )
            elif self.gemini_client:
                logger.warning(f"Provider {self.provider} no válido, usando Gemini")
                return self.gemini_client
            else:
                raise ValueError(f"No hay LLM disponible (provider: {self.provider})")

    async def _check_ollama(self) -> bool:
        """
        Verifica si Ollama está disponible
        
        Returns:
            True si Ollama responde
        """
        try:
            response = await self.http_client.get(
                f"{self.ollama_base_url}/api/tags",
                timeout=5.0
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Ollama no disponible: {e}")
            return False
    
    async def list_ollama_models(self) -> List[str]:
        """
        Lista los modelos disponibles en Ollama
        
        Returns:
            Lista de nombres de modelos
        """
        try:
            response = await self.http_client.get(f"{self.ollama_base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except Exception as e:
            logger.error(f"Error listando modelos Ollama: {e}")
            return []
    
    async def download_ollama_model(self, model_name: str) -> bool:
        """
        Descarga un modelo en Ollama
        
        Args:
            model_name: Nombre del modelo (ej: 'llama3')
            
        Returns:
            True si se descargó correctamente
        """
        try:
            url = f"{self.ollama_base_url}/api/pull"
            payload = {"name": model_name}
            
            response = await self.http_client.post(url, json=payload)
            response.raise_for_status()
            
            logger.info(f"✓ Modelo {model_name} descargado")
            return True
        except Exception as e:
            logger.error(f"Error descargando modelo {model_name}: {e}")
            return False
    
    async def test_connection(self) -> Dict[str, Any]:
        """
        Prueba la conexión con los servicios LLM
        
        Returns:
            Dict con estado de cada proveedor
        """
        result = {
            "provider": self.provider,
            "providers_available": []
        }
        
        # Test Ollama
        ollama_available = await self._check_ollama()
        if ollama_available:
            result["providers_available"].append("ollama")
            models = await self.list_ollama_models()
            result["ollama"] = {
                "status": "ok",
                "base_url": self.ollama_base_url,
                "default_model": self.ollama_model,
                "models_available": models
            }
        else:
            result["ollama"] = {
                "status": "unavailable",
                "base_url": self.ollama_base_url
            }
        
        # Test Gemini
        if self.gemini_api_key:
            result["providers_available"].append("gemini")
            result["gemini"] = {
                "status": "configured",
                "api_key_set": True
            }
        else:
            result["gemini"] = {
                "status": "not_configured",
                "api_key_set": False
            }
        
        result["active_provider"] = self.provider
        result["status"] = "ok" if result["providers_available"] else "no_providers"
        
        return result
    
    async def close(self):
        """Cierra las conexiones HTTP"""
        await self.http_client.aclose()
    
    def __del__(self):
        """Limpieza al destruir el objeto"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.close())
            else:
                loop.run_until_complete(self.close())
        except Exception:
            pass