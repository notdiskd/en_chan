from openai import AsyncOpenAI
import numpy as np
from config import llm, diary

_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=llm["api_key"],
)

async def get_embedding(text: str) -> list[float]:
    response = await _client.embeddings.create(
        model=diary["embedding_model"],
        input=text,
    )
    return response.data[0].embedding

def cosine_similarity(a: list[float], b: np.ndarray) -> float:
    a = np.array(a)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

def serialize_embedding(vec: list[float]) -> bytes:
    return np.array(vec, dtype=np.float32).tobytes()

def deserialize_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)