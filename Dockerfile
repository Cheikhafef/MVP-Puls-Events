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

# Etape 3 : core
RUN pip install --no-cache-dir streamlit==1.55.0 python-dotenv requests pandas

# Etape 4 : LangChain
RUN pip install --no-cache-dir langchain langchain-mistralai langchain-huggingface langchain-community langchain-qdrant

# Etape 5 : embeddings + qdrant
RUN pip install --no-cache-dir sentence-transformers==2.7.0 faiss-cpu qdrant-client

# Etape 6 : smolagents (sans upgrader tokenizers)
RUN pip install --no-cache-dir smolagents duckduckgo-search ddgs litellm

# Etape 7 : forcer tokenizers 0.19.1 apres litellm
RUN pip install --no-cache-dir tokenizers==0.19.1

# Etape 8 : chainlit
RUN pip install --no-cache-dir chainlit
RUN pip install --no-cache-dir asyncpg
# Copie du code
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY .chainlit/ ./.chainlit/
COPY chainlit.md .
COPY .env .

EXPOSE 8000

# Lance Chainlit
CMD ["chainlit", "run", "scripts/chatbot_chainlit.py", \
     "--port", "8000", "--host", "0.0.0.0"]
