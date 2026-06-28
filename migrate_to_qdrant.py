"""
migrate_to_qdrant.py
====================
Migration des embeddings FAISS vers Qdrant.

Ce script :
1. Charge l'index FAISS existant
2. Extrait tous les documents et leurs vecteurs
3. Les insere dans Qdrant (port 6333)

Usage :
    python scripts/migrate_to_qdrant.py

Prerequis :
    - Qdrant doit tourner : docker run -p 6333:6333 qdrant/qdrant
    - Index FAISS doit exister : data/index/faiss_index/
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
FAISS_PATH      = "data/index/faiss_index"
QDRANT_URL      = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "puls_events")

print("=== Migration FAISS -> Qdrant ===")
print(f"Source      : {FAISS_PATH}")
print(f"Destination : {QDRANT_URL} / collection '{COLLECTION_NAME}'")
print()

# 1. Chargement FAISS
print("Chargement de l'index FAISS...")
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

if not os.path.exists(FAISS_PATH):
    print(f"ERREUR : Index FAISS introuvable : {FAISS_PATH}")
    print("Lancez d'abord : python scripts/build_vector_db.py")
    sys.exit(1)

embeddings = HuggingFaceEmbeddings(
    model_name=EMBEDDING_MODEL,
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)

faiss_db = FAISS.load_local(
    FAISS_PATH,
    embeddings,
    allow_dangerous_deserialization=True
)

nb_vecteurs = faiss_db.index.ntotal
print(f"Index FAISS charge — {nb_vecteurs} vecteurs")

# 2. Extraction des documents
print("Extraction des documents...")
docs = list(faiss_db.docstore._dict.values())
print(f"{len(docs)} documents extraits")

# 3. Migration vers Qdrant
print(f"Migration vers Qdrant ({QDRANT_URL})...")

from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

# Connexion Qdrant
#client = QdrantClient(url=QDRANT_URL)
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY if QDRANT_API_KEY else None)
# Supprime la collection si elle existe deja
collections = [c.name for c in client.get_collections().collections]
if COLLECTION_NAME in collections:
    print(f"Collection '{COLLECTION_NAME}' existante — suppression...")
    client.delete_collection(COLLECTION_NAME)

# Cree la collection
client.create_collection(
    collection_name=COLLECTION_NAME,
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
)
print(f"Collection '{COLLECTION_NAME}' creee (dim=384, distance=COSINE)")

# Insertion des documents par batch
batch_size = 100
total      = len(docs)

qdrant_db = QdrantVectorStore(
    client=client,
    collection_name=COLLECTION_NAME,
    embedding=embeddings,
)

for i in range(0, total, batch_size):
    batch = docs[i:i + batch_size]
    qdrant_db.add_documents(batch)
    print(f"  Insere {min(i + batch_size, total)}/{total} documents...")

print()
print("=== Migration terminee avec succes ! ===")
print(f"Collection Qdrant : '{COLLECTION_NAME}'")
print(f"Documents inseres : {total}")
print(f"Dashboard         : {QDRANT_URL}/dashboard")
