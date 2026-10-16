from repomind.config import config
from repomind.observability.logger import get_logger


logger = get_logger(__name__)




def get_retriever():
   mode = config["rag"]["mode"]
   provider = config["vector_store"]["provider"]


   if mode == "hybrid" and provider == "qdrant":
       from .hybrid_qdrant import retrieve
   elif provider == "qdrant":
       from .semantic_qdrant import retrieve
   elif mode == "hybrid":
       from .hybrid_chroma import retrieve   # dense + BM25 with RRF, no extra service needed
   else:
       from .semantic_chroma import retrieve


   return retrieve
