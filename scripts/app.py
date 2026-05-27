"""
app.py — MVP Puls-Events
========================
Sprint 1 + Sprint 2 + Sprint 3 FINAL
- Chat multi-tours + Mémoire conversationnelle
- Géolocalisation automatique + Menu déroulant 40 villes
- Fallback smolagents (sites ciblés)
- Fenêtre temporelle -12 mois / +12 mois 
- Historique conservé avec tag ville par message
- Monitoring + temps d'exécution
"""

import os
import re
import logging
import requests
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv

from langchain_mistralai import ChatMistralAI
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from agent_search import search_events_web

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "open-mistral-7b")
MISTRAL_TEMPERATURE = float(os.getenv("MISTRAL_TEMPERATURE", 0.4))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
FAISS_MIN_RESULTS = int(os.getenv("FAISS_MIN_RESULTS", 2))

if not MISTRAL_API_KEY:
    st.error("❌ MISTRAL_API_KEY manquante dans le fichier .env")
    st.stop()

FAISS_MIN_RESULTS = 2

VILLES_FRANCE = [
    "Paris", "Lyon", "Marseille", "Toulouse", "Nice", "Nantes", "Montpellier",
    "Strasbourg", "Bordeaux", "Lille", "Rennes", "Reims", "Saint-Étienne",
    "Toulon", "Grenoble", "Dijon", "Angers", "Nîmes", "Villeurbanne", "Clermont-Ferrand",
    "Le Havre", "Aix-en-Provence", "Brest", "Tours", "Amiens", "Limoges",
    "Perpignan", "Metz", "Besançon", "Orléans", "Mulhouse", "Rouen", "Caen",
    "Nancy", "Avignon", "Poitiers", "Pau", "La Rochelle", "Calais", "Troyes",
]

st.set_page_config(
    page_title="Puls-Events Chatbot - MVP",
    page_icon="🎉",
    layout="centered"
)

# ──────────────────────────────────────────────────
# Géolocalisation automatique
# ──────────────────────────────────────────────────
def detect_city_from_ip() -> str:
    try:
        r = requests.get("http://ip-api.com/json/", timeout=4).json()
        if r.get("status") == "success":
            city = r.get("city", "Paris")
            for v in VILLES_FRANCE:
                if v.lower() in city.lower() or city.lower() in v.lower():
                    return v
            return city
    except Exception as e:
        logger.warning(f"Géolocalisation IP échouée : {e}")
    return "Paris"

# ──────────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Paramètres MVP")
    st.markdown("#### 📍 Ma localisation")

    if st.button("📡 Détecter ma ville automatiquement", use_container_width=True):
        with st.spinner("Détection en cours..."):
            ville_detectee = detect_city_from_ip()
            st.session_state["ville_selectionnee"] = ville_detectee
        st.success(f"📡 Ville détectée : **{ville_detectee}**")
        st.rerun()

    ville_default = st.session_state.get("ville_selectionnee", "Paris")
    idx = VILLES_FRANCE.index(ville_default) if ville_default in VILLES_FRANCE else 0

    # Pas de on_change — on conserve l'historique (mémoire conversationnelle)
    # Chaque message est tagué avec sa ville pour éviter la confusion
    ville = st.selectbox(
        "Ma ville",
        options=VILLES_FRANCE,
        index=idx,
        help="L'historique est conservé. Chaque réponse affiche sa ville d'origine."
    )
    st.session_state["ville_selectionnee"] = ville
    st.caption("💡 L'historique est conservé entre les villes.")

    st.divider()

    use_agent = st.toggle(
        "🌐 Recherche web (smolagents)",
        value=True,
        help=f"Active si FAISS trouve < {FAISS_MIN_RESULTS} résultats."
    )

    st.divider()

    if st.button("🗑️ Nouvelle conversation", use_container_width=True):
        st.session_state.messages  = []
        st.session_state.memory    = []
        st.session_state.query_log = []
        logger.info("Session réinitialisée.")
        st.rerun()

    # Dashboard monitoring
    if "query_log" in st.session_state and st.session_state.query_log:
        st.divider()
        st.markdown("### 📊 Surveillance")
        logs        = st.session_state.query_log
        nb_queries  = len(logs)
        avg_latency = sum(l["latency_ms"] for l in logs) / nb_queries
        nb_agent    = sum(1 for l in logs if l["source"] == "agent")
        nb_faiss    = nb_queries - nb_agent

        st.metric("Requêtes", nb_queries)
        st.metric("Latence moy.", f"{avg_latency:.0f} ms")
        col1, col2 = st.columns(2)
        col1.metric("🗄️ FAISS", nb_faiss)
        col2.metric("🌐 Agent", nb_agent)

    st.caption("Puls-Events MVP v1.0 — Mai 2026")

# ──────────────────────────────────────────────────
# Ressources (mis en cache)
# ──────────────────────────────────────────────────
@st.cache_resource
def load_db() -> FAISS:
    embeddings = HuggingFaceEmbeddings(
        model_name= EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    return FAISS.load_local(
        "data/index/faiss_index",
        embeddings,
        allow_dangerous_deserialization=True
    )

@st.cache_resource
def load_llm() -> ChatMistralAI:
    return ChatMistralAI(
        model=MISTRAL_MODEL,
        api_key=MISTRAL_API_KEY,
        temperature=MISTRAL_TEMPERATURE
    )

vector_db = load_db()
llm       = load_llm()

# ──────────────────────────────────────────────────
# Session state
# ──────────────────────────────────────────────────
if "memory"    not in st.session_state: st.session_state.memory    = []
if "messages"  not in st.session_state: st.session_state.messages  = []
if "query_log" not in st.session_state: st.session_state.query_log = []

# ──────────────────────────────────────────────────
# Utilitaires
# ──────────────────────────────────────────────────
MOIS_MAP = {
    "janvier":"01","fevrier":"02","février":"02","mars":"03","avril":"04","mai":"05",
    "juin":"06","juillet":"07","aout":"08","août":"08","septembre":"09",
    "octobre":"10","novembre":"11","decembre":"12","décembre":"12",
    "january":"01","february":"02","march":"03","april":"04","june":"06",
    "july":"07","august":"08","september":"09","october":"10","november":"11","december":"12",
}

def detect_date_filter(question):
    q, mois_found, annee_found = question.lower(), None, None
    for mot, num in MOIS_MAP.items():
        if mot in q:
            mois_found = num
            break
    m = re.search(r"\b(202[0-9])\b", q)
    if m: annee_found = m.group(1)
    return mois_found, annee_found

def parse_event(text):
    n = re.search(r"[EÉ]v[eé]nement\s*:\s*(.*?)\.", text, re.IGNORECASE)
    d = re.search(r"Date\s*:\s*(\d{2}/\d{2}/\d{4})", text)
    l = re.search(r"Lieu\s*:\s*(.*?)\.", text)
    if not n or not d: return None
    return {"name": n.group(1).strip(), "date": d.group(1).strip(),
            "lieu": l.group(1).strip() if l else "Inconnu"}

def filter_events(docs, mois_filter=None, annee_filter=None, ville_filter=None):
    now          = datetime.now()
    one_year_ago = now.replace(year=now.year - 1)
    one_year_fut = now.replace(year=now.year + 1)  
    seen, events = set(), []
    for doc in docs:
        ev = parse_event(doc.page_content)
        if not ev: continue
        try: ev_date = datetime.strptime(ev["date"], "%d/%m/%Y")
        except ValueError: continue
        # Fenêtre -12 mois / +12 mois
        if not (one_year_ago.date() <= ev_date.date() <= one_year_fut.date()): continue
        if mois_filter and f"{ev_date.month:02d}" != mois_filter: continue
        if annee_filter and str(ev_date.year) != annee_filter: continue
        if ville_filter and ville_filter.lower() not in ev["lieu"].lower(): continue
        line = f"{ev['name']} - {ev['date']} - {ev['lieu']}"
        if line not in seen:
            seen.add(line)
            events.append(line)
    return events

def build_prompt(question, events, ville):
    # Mémoire conversationnelle — 10 derniers échanges
    history_text = ""
    for msg in st.session_state.memory[-10:]:
        role = "Utilisateur" if msg["role"] == "user" else "Assistant"
        history_text += f"{role} : {msg['content']}\n"
    contexte = "\n".join(events[:8])
    prompt = (
        f"[INST] Tu es l'assistant Puls-Events, expert en événements culturels à {ville}.\n"
        "Réponds en français, de façon naturelle, concise et polie.\n"
        "Utilise UNIQUEMENT les événements de la liste ci-dessous.\n"
        "Si la question s'appuie sur l'historique, utilise-le.\n\n"
    )
    if history_text:
        prompt += f"HISTORIQUE :\n{history_text}\n"
    prompt += f"ÉVÉNEMENTS ({ville}) :\n{contexte}\n\nQUESTION : {question} [/INST]"
    return prompt

# ──────────────────────────────────────────────────
# Interface chat
# ──────────────────────────────────────────────────
st.title("🎉 Puls-Events — Votre Assistant Privé")
#st.caption(f"📍 Recherche à **{ville}**")

# Affichage historique — chaque message affiche sa ville d'origine
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and "latency_ms" in msg:
            ville_msg = msg.get("ville", "")
            st.caption(f"⏱️ {msg['latency_ms']:.0f} ms · {msg['source']} · 📍 {ville_msg}")

# ──────────────────────────────────────────────────
# Traitement de la question
# ──────────────────────────────────────────────────
question = st.chat_input("Qu'allez-vous faire ce week-end ?")

if question:
    start_time         = datetime.now()
    fallback_activated = False
    source_used        = "faiss"

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):

        # Étape 1 : FAISS
        with st.spinner("🔍 Recherche dans la base locale..."):
            mois_filter, annee_filter = detect_date_filter(question)
            if mois_filter or annee_filter:
                all_docs = vector_db.similarity_search(question, k=vector_db.index.ntotal)
            else:
                retriever = vector_db.as_retriever(
                    search_type="mmr",
                    search_kwargs={"k": 15, "fetch_k": 40}
                )
                all_docs = retriever.invoke(question)
            ville_filter = ville.strip() if ville.strip() else None
            events = filter_events(all_docs, mois_filter, annee_filter, ville_filter)

        # Étape 2 : Fallback smolagents
        if len(events) < FAISS_MIN_RESULTS and use_agent:
            fallback_activated = True
            source_used        = "agent"

            now_calc    = datetime.now()
            annee_cible = now_calc.year
            if mois_filter:
                mois_num = int(mois_filter)
                annee_cible = now_calc.year if mois_num <= now_calc.month else now_calc.year + 1

            with st.spinner(f"⚠️ Recherche web en cours pour {ville}..."):
                if mois_filter:
                    question_enrichie = (
                        f"Trouve des événements réels à {ville} en {question} {annee_cible}. "
                        f"ATTENTION : cherche UNIQUEMENT à {ville}, année {annee_cible}."
                    )
                else:
                    question_enrichie = (
                        f"{question}. Cherche à {ville} uniquement."
                    )
                web_results = search_events_web(question_enrichie, ville=ville, timeout=20)

            if web_results:
                response_text = web_results
            else:
                response_text = (
                    f"Je n'ai pas trouvé d'événements à {ville}. "
                    "Essayez une autre ville ou reformulez votre question."
                )
        else:
            # Étape 3 : Génération RAG
            with st.spinner("✍️ Génération de la réponse..."):
                prompt        = build_prompt(question, events, ville)
                response      = llm.invoke(prompt)
                response_text = response.content

        duration_ms  = (datetime.now() - start_time).total_seconds() * 1000
        source_label = "🌐 Web (smolagents)" if fallback_activated else f"🗄️ Base locale — {len(events)} résultats"

        st.markdown(response_text)
        st.caption(f"⏱️ **{duration_ms:.0f} ms** · {source_label} · 📍 {ville}")

        if fallback_activated:
            st.info("ℹ️ Résultats issus de la recherche web en temps réel.")

        if events and not fallback_activated:
            with st.expander(f"📋 Sources ({len(events)} événements)"):
                for ev in events:
                    st.write(f"• {ev}")

    # Monitoring
    st.session_state.query_log.append({
        "timestamp":  datetime.now().strftime("%H:%M:%S"),
        "question":   question[:60],
        "nb_results": len(events),
        "source":     source_used,
        "latency_ms": duration_ms,
    })
    logger.info(f"Query: '{question}' | Ville: {ville} | Source: {source_used} | Latency: {duration_ms:.0f}ms")

    # Mémoire conversationnelle
    st.session_state.memory.append({"role": "user",      "content": question})
    st.session_state.memory.append({"role": "assistant", "content": response_text})
    st.session_state.messages.append({
        "role":       "assistant",
        "content":    response_text,
        "latency_ms": duration_ms,
        "source":     source_label,
        "ville":      ville,  # Tag ville pour identifier l'origine de chaque réponse
    })
