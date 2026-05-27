"""
test_events.py
==============
Tests unitaires du pipeline RAG Puls-Events.

Valide que les données collectées et indexées respectent les contraintes
des étapes 2 (préprocessing Open Agenda) et 3 (indexation FAISS).

Usage :
    python -m pytest scripts/test_events.py -v
    python -m pytest scripts/test_events.py -v --tb=short
"""

import re
import os
import pytest
import pandas as pd
from datetime import datetime
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS


# ──────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────
INDEX_PATH = "data/index/faiss_index"
CSV_PATH   = "data/events_clean.csv"

COLONNES_ATTENDUES = [
    "title", "description", "city",
    "address", "postal_code", "start_date", "content"
]


# ──────────────────────────────────────────────────
# FIXTURES pytest
# ──────────────────────────────────────────────────
@pytest.fixture(scope="session")
def df():
    """Charge le CSV des événements une seule fois pour toute la session."""
    assert os.path.exists(CSV_PATH), (
        f"Fichier CSV introuvable : {CSV_PATH}\n"
        "Lancez d'abord : python scripts/fetch_events.py"
    )
    dataframe = pd.read_csv(CSV_PATH)
    dataframe["start_date"] = pd.to_datetime(
        dataframe["start_date"], utc=True, errors="coerce"
    )
    return dataframe


@pytest.fixture(scope="session")
def faiss_db():
    """Charge l'index FAISS une seule fois pour toute la session."""
    assert os.path.exists(INDEX_PATH), (
        f"Index FAISS introuvable : {INDEX_PATH}\n"
        "Lancez d'abord : python scripts/build_vector_db.py"
    )
    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    return FAISS.load_local(
        INDEX_PATH,
        embeddings,
        allow_dangerous_deserialization=True
    )


# ──────────────────────────────────────────────────
# TESTS — ÉTAPE 2 : Dataset CSV
# ──────────────────────────────────────────────────
def test_colonnes_presentes(df):
    """Vérifie que le CSV contient toutes les colonnes attendues."""
    manquantes = [col for col in COLONNES_ATTENDUES if col not in df.columns]
    assert not manquantes, (
        f"Colonnes manquantes : {manquantes}\n"
        f"Colonnes présentes : {list(df.columns)}"
    )


def test_pas_de_nan(df):
    """Vérifie qu'aucun NaN n'est présent dans les colonnes critiques."""
    nan_counts = df[["title", "description", "start_date"]].isna().sum()
    assert nan_counts.sum() == 0, (
        f"Valeurs NaN détectées :\n{nan_counts.to_dict()}"
    )


def test_dates_periode_valide(df):
    """
    Vérifie que tous les événements sont dans la fenêtre temporelle valide.

    Consigne encadreur (mise à jour) :
        Fenêtre : aujourd'hui - 12 mois → aujourd'hui + 12 mois
        Les événements passés récents ET futurs proches sont autorisés.

    Args:
        df (pd.DataFrame): Fixture du dataset CSV.

    Raises:
        AssertionError: Si des événements hors période sont détectés.
    """
    now_utc         = pd.Timestamp.now(tz="UTC")
    one_year_ago_ts = now_utc - pd.Timedelta(days=365)
    one_year_fut_ts = now_utc + pd.Timedelta(days=365)

    trop_vieux = df[df["start_date"] < one_year_ago_ts]
    trop_futur = df[df["start_date"] > one_year_fut_ts]

    assert len(trop_vieux) == 0, (
        f"{len(trop_vieux)} événement(s) de plus d'un an détecté(s).\n"
        f"Exemple : {trop_vieux[['title','start_date']].head(3).to_string()}"
    )
    assert len(trop_futur) == 0, (
        f"{len(trop_futur)} événement(s) au-delà de +12 mois détecté(s).\n"
        f"Exemple : {trop_futur[['title','start_date']].head(3).to_string()}"
    )


def test_perimetre_paris(df):
    """Vérifie que tous les événements appartiennent au périmètre Paris."""
    cp_str     = df["postal_code"].astype(str)
    city_lower = df["city"].astype(str).str.lower()

    hors_paris = df[
        ~cp_str.str.startswith("75") &
        ~city_lower.str.contains("paris", na=False)
    ]
    assert len(hors_paris) == 0, (
        f"{len(hors_paris)} événement(s) hors Paris détecté(s).\n"
        f"Exemples :\n{hors_paris[['title','city','postal_code']].head(3).to_string()}"
    )


def test_pas_de_doublons(df):
    """Vérifie l'absence de doublons sur la clé (title, start_date)."""
    doublons = df.duplicated(subset=["title", "start_date"]).sum()
    assert doublons == 0, (
        f"{doublons} doublon(s) détecté(s) sur la clé (title, start_date)."
    )


# ──────────────────────────────────────────────────
# TESTS — ÉTAPE 3 : Index FAISS
# ──────────────────────────────────────────────────
def test_index_faiss_non_vide(faiss_db):
    """Vérifie que l'index FAISS contient au moins 100 vecteurs."""
    nb_vecteurs = faiss_db.index.ntotal
    assert nb_vecteurs >= 100, (
        f"Index FAISS trop petit : {nb_vecteurs} vecteurs (minimum : 100).\n"
        "Relancez : python scripts/build_vector_db.py"
    )


def test_chunks_faiss_coherents(faiss_db):
    """
    Vérifie la cohérence sémantique des chunks dans l'index FAISS.

    Fenêtre de validation mise à jour : -12 mois / +12 mois
    (cohérence avec la consigne encadreur et filter_events dans app.py)

    Teste 500 chunks sur deux critères :
        1. Date valide dans la fenêtre -12 mois / +12 mois
        2. Localisation parisienne (lieu contient 'paris' ou '75')

    Seuil : 50% minimum (justifié car seul le 1er chunk contient
    les champs structurés, les autres sont descriptifs).
    """
    now          = datetime.now()
    one_year_ago = now.replace(year=now.year - 1)
    one_year_fut = now.replace(year=now.year + 1)  # Consigne encadreur : +12 mois

    sample_size  = min(faiss_db.index.ntotal, 500)
    docs         = faiss_db.similarity_search("evenement culturel paris", k=sample_size)
    total_chunks = len(docs)

    dates_valides = 0
    loc_paris     = 0

    for doc in docs:
        text       = doc.page_content
        date_match = re.search(r"Date\s*:\s*(\d{2}/\d{2}/\d{4})", text)
        lieu_match = re.search(r"Lieu\s*:\s*(.*?)\.", text)

        try:
            if date_match:
                event_date = datetime.strptime(date_match.group(1), "%d/%m/%Y")
                if one_year_ago.date() <= event_date.date() <= one_year_fut.date():
                    dates_valides += 1
            if lieu_match:
                lieu = lieu_match.group(1).lower()
                if "paris" in lieu or "75" in lieu:
                    loc_paris += 1
        except Exception:
            pass

    taux_dates = dates_valides / total_chunks if total_chunks > 0 else 0
    taux_loc   = loc_paris     / total_chunks if total_chunks > 0 else 0

    assert taux_dates >= 0.50, (
        f"Taux de dates valides insuffisant : {taux_dates*100:.1f}% < 50%\n"
        f"({dates_valides}/{total_chunks} chunks avec une date valide)"
    )
    assert taux_loc >= 0.50, (
        f"Taux localisation Paris insuffisant : {taux_loc*100:.1f}% < 50%\n"
        f"({loc_paris}/{total_chunks} chunks avec 'paris' ou '75')"
    )