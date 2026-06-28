FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    PIP_DEFAULT_TIMEOUT=200 \
    PIP_RETRIES=5

WORKDIR /app

# Etape 1 : torch CPU
RUN pip install --no-cache-dir \
    torch==2.1.0 \
    --index-url https://download.pytorch.org/whl/cpu

# Etape 2 : versions compatibles fixes
RUN pip install --no-cache-dir \
    transformers==4.41.2 \
    tokenizers==0.19.1 \
    accelerate \
    numpy==1.26.4

# Etape 3 : core (PAS de streamlit — plus utilisé)
RUN pip install --no-cache-dir python-dotenv requests pandas

# Etape 4 : LangChain
RUN pip install --no-cache-dir \
    langchain \
    langchain-mistralai \
    langchain-huggingface \
    langchain-community \
    langchain-qdrant

# Etape 5 : embeddings + qdrant (PAS de faiss-cpu — on utilise Qdrant Cloud)
RUN pip install --no-cache-dir sentence-transformers==2.7.0 qdrant-client

# Etape 6 : smolagents
RUN pip install --no-cache-dir smolagents duckduckgo-search ddgs litellm

# Etape 7 : forcer tokenizers 0.19.1 apres litellm
RUN pip install --no-cache-dir tokenizers==0.19.1

# Etape 8 : chainlit + DB layer (greenlet OBLIGATOIRE pour SQLAlchemy async)
RUN pip install --no-cache-dir chainlit asyncpg sqlalchemy greenlet

# Etape 9 : pre-telecharger le modele embedding (evite timeout au demarrage)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Copie du code — structure racine (PAS de sous-dossier scripts/)
COPY chatbot_chainlit.py .
COPY agent_search.py .
COPY .chainlit/ ./.chainlit/
COPY chainlit.md .
# NE PAS copier .env — les variables sont injectees par Azure Container Apps

EXPOSE 8000

# Lance Chainlit — fichier a la RACINE
CMD ["chainlit", "run", "chatbot_chainlit.py", \
     "--port", "8000", "--host", "0.0.0.0"]