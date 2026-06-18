import asyncio
import sys

# DOIT etre en premier — fix ProactorEventLoop Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import os
import re
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv

import chainlit as cl
from chainlit.input_widget import Select, Switch
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from langchain_mistralai import ChatMistralAI
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from agent_search import search_events_web

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MISTRAL_API_KEY   = os.getenv("MISTRAL_API_KEY")
QDRANT_URL        = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "puls_events")
QDRANT_API_KEY    = os.getenv("QDRANT_API_KEY", "")
EMBEDDING_MODEL   = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
MISTRAL_MODEL     = os.getenv("MISTRAL_MODEL", "open-mistral-7b")
FAISS_MIN_RESULTS = int(os.getenv("FAISS_MIN_RESULTS", 2))
DATABASE_URL      = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:chainlit123@localhost:5432/chainlit"
)

def detect_city_from_ip(client_ip: str = None) -> str:
    """Détecte la ville via IP client — côté serveur, zéro permission navigateur."""
    try:
        # Utiliser l'IP du client si disponible, sinon autodetect
        url = f"https://ipapi.co/{client_ip}/json/" if client_ip else "https://ipapi.co/json/"
        r = requests.get(url, timeout=3, headers={"User-Agent": "PulsEvents/1.0"})
        if r.status_code == 200:
            data = r.json()
            city = data.get("city", "")
            logger.info(f"GeoIP: ip={client_ip}, city={city}")
            for v in VILLES_FRANCE:
                if city and (v.lower() in city.lower() or city.lower() in v.lower()):
                    return v
            if city:
                return city
    except Exception as e:
        logger.error(f"GeoIP error: {e}")
    return "Paris"


VILLES_FRANCE = [
    "Paris", "Lyon", "Marseille", "Toulouse", "Nice", "Nantes", "Montpellier",
    "Strasbourg", "Bordeaux", "Lille", "Rennes", "Reims", "Saint-Etienne",
    "Toulon", "Grenoble", "Dijon", "Angers", "Nimes", "Clermont-Ferrand",
    "Le Havre", "Aix-en-Provence", "Brest", "Tours", "Amiens", "Limoges",
    "Perpignan", "Metz", "Besancon", "Orleans", "Rouen", "Caen",
    "Nancy", "Avignon", "Poitiers", "Pau", "La Rochelle", "Calais",
]

MOIS_MAP = {
    "janvier":"01","fevrier":"02","mars":"03","avril":"04","mai":"05",
    "juin":"06","juillet":"07","aout":"08","septembre":"09",
    "octobre":"10","novembre":"11","decembre":"12",
}


# ──────────────────────────────────────────────────
# DATA LAYER — UN SEUL décorateur, sans ssl_require
# ──────────────────────────────────────────────────
@cl.data_layer
def get_data_layer():
    return SQLAlchemyDataLayer(conninfo=DATABASE_URL)


# ──────────────────────────────────────────────────
# AUTHENTIFICATION
# ──────────────────────────────────────────────────
@cl.password_auth_callback
def auth_callback(username: str, password: str):
    users = {
        "demo":  "pulsevents2026",
        "remy":  "encadreur2026",
        "afef":  "afef2026",
    }
    if username in users and users[username] == password:
        return cl.User(identifier=username, metadata={"role": "user"})
    return None


# ──────────────────────────────────────────────────
# UTILITAIRES
# ──────────────────────────────────────────────────
def detect_city_from_gps(lat: float, lon: float) -> str:
    try:
        r = requests.get(
            f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json",
            headers={"User-Agent": "PulsEvents/1.0"},
            timeout=5
        ).json()
        address = r.get("address", {})
        city = address.get("city") or address.get("town") or address.get("village") or "Paris"
        for v in VILLES_FRANCE:
            if v.lower() in city.lower() or city.lower() in v.lower():
                return v
        return city
    except Exception:
        return "Paris"


def detect_ville_in_question(question: str, ville_session: str) -> str:
    q = question.lower()
    for v in VILLES_FRANCE:
        if v.lower() in q:
            return v
    return ville_session


def is_near_me(question: str) -> bool:
    """Détecte si l'utilisateur demande des événements proches de lui."""
    keywords = ["proche de moi", "autour de moi", "près de moi", "pres de moi",
                "autour", "dans ma ville", "ici", "ma région", "ma region"]
    return any(k in question.lower() for k in keywords)


def detect_date_filter(question):
    q, mois_found, annee_found = question.lower(), None, None
    now = datetime.now()
    annee_courante = str(now.year)

    # Detection saisons → pas de filtre mois unique, on filtre par annee + plage
    if any(s in q for s in ["cet ete", "en ete", "l'ete", "cet été", "l'été"]):
        annee_found = annee_courante
        mois_found = None  # On gere l'ete dans filter_events
        return "ETE", annee_found
    elif any(s in q for s in ["cet hiver", "en hiver", "l'hiver"]):
        annee_found = annee_courante
        return "HIVER", annee_found
    elif any(s in q for s in ["ce printemps", "en printemps"]):
        annee_found = annee_courante
        return "PRINTEMPS", annee_found
    elif any(s in q for s in ["cet automne", "en automne"]):
        annee_found = annee_courante
        return "AUTOMNE", annee_found

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
    # Plage large par défaut : 1 an arrière → 2 ans avant
    # Mais si mois ou année demandés : filtre STRICT, aucune tolérance
    now          = datetime.now()
    one_year_ago = now.replace(year=now.year - 1)
    two_year_fut = now.replace(year=now.year + 2)
    seen, events = set(), []
    for doc in docs:
        ev = parse_event(doc.page_content)
        if not ev: continue
        try: ev_date = datetime.strptime(ev["date"], "%d/%m/%Y")
        except ValueError: continue
        # Plage générale (ignorée si filtre strict demandé)
        if not mois_filter and not annee_filter:
            if not (one_year_ago.date() <= ev_date.date() <= two_year_fut.date()):
                continue
        # Filtres saisonniers
        if mois_filter == "ETE":
            if ev_date.month not in [6, 7, 8]: continue
        elif mois_filter == "HIVER":
            if ev_date.month not in [12, 1, 2]: continue
        elif mois_filter == "PRINTEMPS":
            if ev_date.month not in [3, 4, 5]: continue
        elif mois_filter == "AUTOMNE":
            if ev_date.month not in [9, 10, 11]: continue
        elif mois_filter:
            if f"{ev_date.month:02d}" != mois_filter: continue
        if annee_filter and str(ev_date.year) != annee_filter:
            continue
        if ville_filter and ville_filter.lower() not in ev["lieu"].lower(): continue
        line = f"{ev['name']} - {ev['date']} - {ev['lieu']}"
        if line not in seen:
            seen.add(line)
            events.append(line)
    return events


def build_prompt(question, events, ville, memory):
    history_text = ""
    for msg in memory[-10:]:
        role = "Utilisateur" if msg["role"] == "user" else "Assistant"
        history_text += f"{role} : {msg['content']}\n"
    contexte = "\n".join(events[:8])
    prompt = (
        f"[INST] Tu es l'assistant Puls-Events, expert en evenements culturels a {ville}.\n"
        "Reponds en francais, de facon naturelle et concise.\n"
        "Utilise STRICTEMENT les evenements de la liste ci-dessous. N'invente rien.\n"
        "N'affiche PAS de fenetre temporelle, pas de dates de recherche, pas de recommandations de sites externes.\n"
        "Reponds directement avec les evenements trouves. Si aucun ne correspond, dis : Aucun evenement trouve pour cette periode.\n"
        "Si la question s'appuie sur l'historique, utilise-le.\n\n"
    )
    if history_text:
        prompt += f"HISTORIQUE :\n{history_text}\n"
    prompt += f"EVENEMENTS ({ville}) :\n{contexte}\n\nQUESTION : {question} [/INST]"
    return prompt


async def simulate_streaming(text: str, msg_object: cl.Message):
    for chunk in [text[i:i+4] for i in range(0, len(text), 4)]:
        await msg_object.stream_token(chunk)
        await asyncio.sleep(0.01)


def init_session(ville="Paris"):
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY if QDRANT_API_KEY else None)
    vector_db = QdrantVectorStore(
        client=client, collection_name=QDRANT_COLLECTION, embedding=embeddings
    )
    llm = ChatMistralAI(model=MISTRAL_MODEL, api_key=MISTRAL_API_KEY, temperature=0.2)
    cl.user_session.set("vector_db", vector_db)
    cl.user_session.set("llm",       llm)
    cl.user_session.set("memory",    [])
    cl.user_session.set("ville",     ville)
    cl.user_session.set("use_agent", True)


# ──────────────────────────────────────────────────
# DEMARRAGE DE SESSION
# ──────────────────────────────────────────────────
@cl.on_chat_start
async def on_chat_start():
    user = cl.context.session.user
    nom  = user.identifier if user else "visiteur"

    # ── Détection automatique ville par IP client
    try:
        headers = cl.context.session.http_referer or {}
        client_ip = None
        # Récupérer l'IP réelle depuis les headers de la session
        environ = getattr(cl.context.session, "environ", {})
        client_ip = (
            environ.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or
            environ.get("HTTP_X_REAL_IP", "") or
            environ.get("REMOTE_ADDR", "") or
            None
        )
        if client_ip in ("127.0.0.1", "::1", ""):
            client_ip = None
    except Exception:
        client_ip = None
    ville_detectee = await cl.make_async(detect_city_from_ip)(client_ip)
    init_session(ville_detectee)
    cl.user_session.set("thread_titled", False)

    # ⚙️ Panneau latéral droit — liste complète avec ville détectée présélectionnée
    settings = await cl.ChatSettings([
        Select(
            id="ville_select",
            label="📍 Ma ville",
            values=VILLES_FRANCE,
            initial_value=ville_detectee,
        ),
        Switch(
            id="use_agent",
            label="🌐 Recherche web si aucun résultat local",
            initial=True,
        ),
    ]).send()

    cl.user_session.set("ville",     settings.get("ville_select", ville_detectee))
    cl.user_session.set("use_agent", settings.get("use_agent", True))

    # Boutons villes rapides
    actions = [
        cl.Action(name="set_ville", payload={"v": v}, label=v)
        for v in ["Paris", "Lyon", "Marseille", "Toulouse", "Nice", "Bordeaux", "Lille"]
    ]

    await cl.Message(
        content=(
            f"🎉 **Bienvenue sur Puls-Events, {nom} !**\n\n"
            "Recherchez des événements culturels sur **2025, 2026 et 2027**.\n\n"
            f"📍 Ville actuelle : **{ville_detectee}** — changez via les boutons ou ⚙️.\n"
            "💬 Vos conversations précédentes sont dans la **sidebar à gauche**."
        ),
        actions=actions,
    ).send()


# ──────────────────────────────────────────────────
# CALLBACKS BOUTONS
# ──────────────────────────────────────────────────
@cl.action_callback("set_ville")
async def on_ville_selected(action: cl.Action):
    ville = action.payload.get("v", "Paris")
    cl.user_session.set("ville", ville)
    await cl.Message(content=f"📍 Ville sélectionnée : **{ville}**. Posez votre question !").send()


# GPS callback supprimé — utiliser les boutons de villes


# ──────────────────────────────────────────────────
# MISE A JOUR SETTINGS
# ──────────────────────────────────────────────────
@cl.on_settings_update
async def on_settings_update(settings):
    ville     = settings.get("ville_select", "Paris")
    use_agent = settings.get("use_agent", True)
    cl.user_session.set("ville",     ville)
    cl.user_session.set("use_agent", use_agent)
    await cl.Message(content=f"📍 Ville mise à jour : **{ville}**").send()


# ──────────────────────────────────────────────────
# REPRISE CONVERSATION — CORRECTION PRINCIPALE
# Chainlit stocke les messages dans thread["steps"]
# avec type "user_message" et "assistant_message"
# ──────────────────────────────────────────────────
@cl.on_chat_resume
async def on_chat_resume(thread):
    init_session("Paris")
    memory = []

    steps = []
    if isinstance(thread, dict):
        steps = thread.get("steps", [])
    else:
        steps = getattr(thread, "steps", [])

    for step in steps:
        if isinstance(step, dict):
            step_type   = step.get("type", "")
            step_output = step.get("output", "") or step.get("content", "")
        else:
            step_type   = getattr(step, "type", "")
            step_output = getattr(step, "output", "") or getattr(step, "content", "")

        if not step_output:
            continue

        if step_type == "user_message":
            memory.append({"role": "user", "content": step_output})
            await cl.Message(content=step_output, author="user").send()
        elif step_type == "assistant_message":
            memory.append({"role": "assistant", "content": step_output})
            await cl.Message(content=step_output, author="assistant").send()

    cl.user_session.set("memory", memory[-20:])
    logger.info(f"[resume] {len(memory)} messages rechargés depuis l'historique")


# ──────────────────────────────────────────────────
# MESSAGES
# ──────────────────────────────────────────────────
@cl.on_message
async def on_message(message: cl.Message):

    # Commande GPS
    if message.content.startswith("__GPS__"):
        try:
            coords    = message.content.replace("__GPS__", "").split(",")
            ville_gps = detect_city_from_gps(float(coords[0]), float(coords[1]))
            cl.user_session.set("ville", ville_gps)
            await cl.Message(content=f"📍 Ville détectée : **{ville_gps}**").send()
        except Exception:
            await cl.Message(
                content="❌ Format incorrect. Exemple : `__GPS__48.8566,2.3522`"
            ).send()
        return

    question      = message.content
    ville_session = cl.user_session.get("ville", "Paris")
    vector_db     = cl.user_session.get("vector_db")
    llm           = cl.user_session.get("llm")
    memory        = cl.user_session.get("memory", [])
    use_agent     = cl.user_session.get("use_agent", True)
    start_time    = datetime.now()

    # ── Nommer la conversation avec le 1er vrai message ──
    if not cl.user_session.get("thread_titled", False):
        titre = question[:50].strip()
        try:
            thread_id = cl.context.session.thread_id
            from chainlit.data import get_data_layer
            dl = get_data_layer()
            if dl:
                await dl.update_thread(thread_id=thread_id, name=titre)
        except Exception as e:
            logger.error(f"update_thread error: {e}")
        cl.user_session.set("thread_titled", True)

    # Détecter "proche de moi" → utiliser la ville de session
    if is_near_me(question) and not any(v.lower() in question.lower() for v in VILLES_FRANCE):
        ville = ville_session
        await cl.Message(content=f"📍 Recherche autour de **{ville}**...").send()
    else:
        ville = detect_ville_in_question(question, ville_session)
        if ville != ville_session:
            await cl.Message(content=f"📍 Ville détectée : **{ville}**").send()
            cl.user_session.set("ville", ville)

    # ── Étape 1 : Recherche Qdrant ──
    async with cl.Step(name="🔍 Recherche Qdrant") as step:
        mois_filter, annee_filter = detect_date_filter(question)
        if mois_filter or annee_filter:
            all_docs = vector_db.similarity_search(question, k=200)
        else:
            retriever = vector_db.as_retriever(
                search_type="mmr", search_kwargs={"k": 15, "fetch_k": 40}
            )
            all_docs = retriever.invoke(question)
        ville_filter = ville.strip() if ville.strip() else None
        events = filter_events(all_docs, mois_filter, annee_filter, ville_filter)
        step.output = f"{len(events)} événements trouvés"

    final_msg = cl.Message(content="")
    await final_msg.send()
    fallback_activated = False
    response_text      = ""

    # ── Étape 2a : Fallback Web ──
    if len(events) == 0 and (mois_filter or annee_filter) and not use_agent:
        await cl.Message(
            content=f"ℹ️ Aucun événement trouvé pour cette période à **{ville}**. "
                    "Essayez une autre date ou activez la recherche web via ⚙️."
        ).send()

    if len(events) < FAISS_MIN_RESULTS and use_agent:
        fallback_activated = True
        async with cl.Step(name="🌐 Recherche Web") as step:
            now_calc    = datetime.now()
            annee_cible = now_calc.year
            if mois_filter:
                if mois_filter in ("ETE", "HIVER", "PRINTEMPS", "AUTOMNE"):
                    annee_cible = now_calc.year
                else:
                    mois_num    = int(mois_filter)
                    annee_cible = now_calc.year if mois_num <= now_calc.month else now_calc.year + 1
            q_enrichie  = f"Evenements a {ville} : {question} {annee_cible}. UNIQUEMENT a {ville}."
            web_results = await cl.make_async(search_events_web)(q_enrichie, ville=ville, timeout=20)
            step.output = "Résultats web récupérés"
        response_text = web_results if web_results else f"Aucun événement trouvé à {ville}."
        await simulate_streaming(response_text, final_msg)

    # ── Étape 2b : Génération Mistral ──
    else:
        async with cl.Step(name="✍️ Génération Mistral") as step:
            prompt = build_prompt(question, events, ville, memory)
            async for chunk in llm.astream(prompt):
                token = chunk.content
                if token:
                    response_text += token
                    await final_msg.stream_token(token)
            step.output = "Réponse générée"

    await final_msg.update()

    # ── Méta-infos ──
    duration_ms  = (datetime.now() - start_time).total_seconds() * 1000
    source_label = "🌐 Web" if fallback_activated else f"🗄️ Qdrant ({len(events)})"
    await cl.Message(
        content=f"`⏱️ {duration_ms:.0f} ms` · {source_label} · 📍 **{ville}**"
    ).send()

    if events and not fallback_activated:
        sources = "\n".join([f"• {ev}" for ev in events[:5]])
        await cl.Message(content=f"📋 **Sources :**\n{sources}").send()

    # ── Mise à jour mémoire ──
    memory.append({"role": "user",      "content": question})
    memory.append({"role": "assistant", "content": response_text})
    cl.user_session.set("memory", memory[-20:])
