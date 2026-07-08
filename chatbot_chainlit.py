import asyncio
import sys

# DOIT etre en premier — fix ProactorEventLoop Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import os
import re
import logging
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

import chainlit as cl
from chainlit.input_widget import Select, Switch
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from langchain_mistralai import ChatMistralAI
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from agent_search import search_events_web
import bcrypt
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

# Villes couvertes par Qdrant (Paris uniquement pour l'instant)
VILLES_QDRANT = {"paris"}

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
    "juin":"06","juillet":"07","aout":"08","août":"08","septembre":"09",
    "octobre":"10","novembre":"11","decembre":"12","décembre":"12",
}

MOIS_NOMS = {
    1:"janvier",2:"février",3:"mars",4:"avril",5:"mai",6:"juin",
    7:"juillet",8:"août",9:"septembre",10:"octobre",11:"novembre",12:"décembre"
}


# ──────────────────────────────────────────────────
# DATA LAYER
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
    "demo": bcrypt.hashpw(os.getenv("PASS_DEMO").encode(), bcrypt.gensalt()),
    "remy": bcrypt.hashpw(os.getenv("PASS_REMY").encode(), bcrypt.gensalt()),
    "afef": bcrypt.hashpw(os.getenv("PASS_AFEF").encode(), bcrypt.gensalt()),
}
    if username in users and users[username] == password:
        return cl.User(identifier=username, metadata={"role": "user"})
    return None


# ──────────────────────────────────────────────────
# DETECTION DATE — VERSION STRICTE ET COMPLETE
# Retourne: (date_debut, date_fin, label, is_strict)
# is_strict=True => filtre exact, pas de suggestion hors période
# ──────────────────────────────────────────────────
def detect_date_range(question: str):
    q   = question.lower()
    # Normaliser les accents pour la détection
    q   = q.replace("é","e").replace("è","e").replace("ê","e").replace("à","a").replace("û","u")
    now = datetime.now()

    # ── Dates relatives STRICTES ──
    if "demain" in q:
        d = now + timedelta(days=1)
        debut = d.replace(hour=0,  minute=0,  second=0,  microsecond=0)
        fin   = d.replace(hour=23, minute=59, second=59)
        return debut, fin, f"demain {d.strftime('%d/%m/%Y')}", True

    if "hier" in q:
        d = now - timedelta(days=1)
        debut = d.replace(hour=0,  minute=0,  second=0,  microsecond=0)
        fin   = d.replace(hour=23, minute=59, second=59)
        return debut, fin, f"hier {d.strftime('%d/%m/%Y')}", True

    if any(x in q for x in ["aujourd'hui","aujourd hui","ce soir","ce jour"]):
        debut = now.replace(hour=0,  minute=0,  second=0,  microsecond=0)
        fin   = now.replace(hour=23, minute=59, second=59)
        return debut, fin, f"aujourd'hui {now.strftime('%d/%m/%Y')}", True

    if any(x in q for x in ["ce week-end","ce weekend","week end","weekend","samedi","dimanche"]):
        days_until_sat = (5 - now.weekday()) % 7
        if days_until_sat == 0 and now.weekday() == 5:
            days_until_sat = 0
        sam = (now + timedelta(days=days_until_sat)).replace(hour=0,  minute=0,  second=0,  microsecond=0)
        dim = (sam + timedelta(days=1)).replace(hour=23, minute=59, second=59)
        return sam, dim, f"ce week-end ({sam.strftime('%d/%m')}–{dim.strftime('%d/%m/%Y')})", True

    if any(x in q for x in ["cette semaine","la semaine"]):
        lundi   = (now - timedelta(days=now.weekday())).replace(hour=0,  minute=0,  second=0,  microsecond=0)
        dimanche = (lundi + timedelta(days=6)).replace(hour=23, minute=59, second=59)
        return lundi, dimanche, f"cette semaine ({lundi.strftime('%d/%m')}–{dimanche.strftime('%d/%m/%Y')})", True

    if any(x in q for x in ["ce mois-ci","ce mois"]):
        debut = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            fin = now.replace(day=31, hour=23, minute=59, second=59)
        else:
            fin = (now.replace(month=now.month+1, day=1) - timedelta(days=1)).replace(hour=23, minute=59, second=59)
        return debut, fin, f"ce mois de {MOIS_NOMS[now.month]} {now.year}", True

    if "mois prochain" in q:
        if now.month == 12:
            ms = now.replace(year=now.year+1, month=1, day=1)
        else:
            ms = now.replace(month=now.month+1, day=1)
        debut = ms.replace(hour=0, minute=0, second=0, microsecond=0)
        if ms.month == 12:
            fin = ms.replace(day=31, hour=23, minute=59, second=59)
        else:
            fin = (ms.replace(month=ms.month+1, day=1) - timedelta(days=1)).replace(hour=23, minute=59, second=59)
        return debut, fin, f"le mois prochain ({MOIS_NOMS[ms.month]} {ms.year})", True

    if any(x in q for x in ["annee prochaine","l'annee prochaine","l annee prochaine"]):
        debut = datetime(now.year+1, 1, 1)
        fin   = datetime(now.year+1, 12, 31, 23, 59, 59)
        return debut, fin, f"l'année {now.year+1}", True

    if any(x in q for x in ["cette annee","cette année","en cours"]):
        debut = datetime(now.year, 1, 1)
        fin   = datetime(now.year, 12, 31, 23, 59, 59)
        return debut, fin, f"l'année {now.year}", True

    # ── Saisons STRICTES ──
    annee_saison = now.year
    m = re.search(r"\b(202[0-9])\b", q)
    if m:
        annee_saison = int(m.group(1))

    if any(s in q for s in ["cet ete","en ete","l'ete","l ete","ete 202","cet été","l'été"]):
        debut = datetime(annee_saison, 6, 21)
        fin   = datetime(annee_saison, 9, 22, 23, 59, 59)
        return debut, fin, f"l'été {annee_saison}", True

    if any(s in q for s in ["cet automne","en automne","l'automne","l automne","automne 202"]):
        debut = datetime(annee_saison, 9, 23)
        fin   = datetime(annee_saison, 12, 20, 23, 59, 59)
        return debut, fin, f"l'automne {annee_saison}", True

    if any(s in q for s in ["cet hiver","en hiver","l'hiver","l hiver","hiver 202"]):
        debut = datetime(annee_saison, 12, 21)
        fin   = datetime(annee_saison+1, 3, 19, 23, 59, 59)
        return debut, fin, f"l'hiver {annee_saison}/{annee_saison+1}", True

    if any(s in q for s in ["ce printemps","au printemps","le printemps","en printemps","printemps 202"]):
        debut = datetime(annee_saison, 3, 20)
        fin   = datetime(annee_saison, 6, 20, 23, 59, 59)
        return debut, fin, f"le printemps {annee_saison}", True

    # ── Jour précis (ex: "29 juillet 2026") ──
    jours_map = {
        "premier":1,"1er":1,"1":1,"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,
        "10":10,"11":11,"12":12,"13":13,"14":14,"15":15,"16":16,"17":17,"18":18,
        "19":19,"20":20,"21":21,"22":22,"23":23,"24":24,"25":25,"26":26,"27":27,
        "28":28,"29":29,"30":30,"31":31
    }
    # Pattern: "29 juillet 2026" ou "le 29 juillet"
    m_jour = re.search(r'(\d{1,2})\s+(janvier|fevrier|mars|avril|mai|juin|juillet|aout|août|septembre|octobre|novembre|decembre|décembre)\s*(202[0-9])?', q)
    if m_jour:
        jour_num = int(m_jour.group(1))
        mois_str_j = m_jour.group(2).replace("août","aout").replace("décembre","decembre")
        mois_num_j = int(MOIS_MAP.get(mois_str_j, "01"))
        annee_j    = int(m_jour.group(3)) if m_jour.group(3) else now.year
        try:
            debut = datetime(annee_j, mois_num_j, jour_num, 0, 0, 0)
            fin   = datetime(annee_j, mois_num_j, jour_num, 23, 59, 59)
            return debut, fin, f"le {jour_num} {MOIS_NOMS[mois_num_j]} {annee_j}", True
        except ValueError:
            pass

    # ── Mois + Année PRÉCIS (le plus strict) ──
    mois_trouve = None
    for mot, num in MOIS_MAP.items():
        if mot in q:
            mois_trouve = int(num)
            break

    annee_trouve = None
    m2 = re.search(r"\b(202[0-9])\b", q)
    if m2:
        annee_trouve = int(m2.group(1))

    if mois_trouve and annee_trouve:
        debut = datetime(annee_trouve, mois_trouve, 1)
        if mois_trouve == 12:
            fin = datetime(annee_trouve, 12, 31, 23, 59, 59)
        else:
            fin = (datetime(annee_trouve, mois_trouve+1, 1) - timedelta(days=1)).replace(hour=23, minute=59, second=59)
        return debut, fin, f"{MOIS_NOMS[mois_trouve]} {annee_trouve}", True

    if mois_trouve and not annee_trouve:
        annee_cible = now.year if mois_trouve >= now.month else now.year + 1
        debut = datetime(annee_cible, mois_trouve, 1)
        if mois_trouve == 12:
            fin = datetime(annee_cible, 12, 31, 23, 59, 59)
        else:
            fin = (datetime(annee_cible, mois_trouve+1, 1) - timedelta(days=1)).replace(hour=23, minute=59, second=59)
        return debut, fin, f"{MOIS_NOMS[mois_trouve]} {annee_cible}", True

    if annee_trouve and not mois_trouve:
        debut = datetime(annee_trouve, 1, 1)
        fin   = datetime(annee_trouve, 12, 31, 23, 59, 59)
        return debut, fin, f"l'année {annee_trouve}", True

    # ── Aucune date détectée → fenêtre large NON STRICTE ──
    debut = now - timedelta(days=30)
    fin   = now + timedelta(days=365)
    return debut, fin, None, False  # is_strict=False


def detect_city_from_ip(client_ip: str = None) -> str:
    try:
        url = f"https://ipapi.co/{client_ip}/json/" if client_ip else "https://ipapi.co/json/"
        r = requests.get(url, timeout=3, headers={"User-Agent": "PulsEvents/1.0"})
        if r.status_code == 200:
            data = r.json()
            city = data.get("city", "")
            for v in VILLES_FRANCE:
                if city and (v.lower() in city.lower() or city.lower() in v.lower()):
                    return v
            if city:
                return city
    except Exception as e:
        logger.error(f"GeoIP error: {e}")
    return "Paris"


def detect_city_from_gps(lat: float, lon: float) -> str:
    try:
        r = requests.get(
            f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json",
            headers={"User-Agent": "PulsEvents/1.0"}, timeout=5
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
    keywords = ["proche de moi","autour de moi","près de moi","pres de moi",
                "autour","dans ma ville","ici","ma région","ma region"]
    return any(k in question.lower() for k in keywords)


def ville_dans_qdrant(ville: str) -> bool:
    """Retourne True si la ville est couverte par Qdrant."""
    return ville.lower().strip() in VILLES_QDRANT


def filter_events(docs, date_debut: datetime, date_fin: datetime,
                  ville_filter=None, is_strict=True):
    """
    Filtre strict par plage de dates exacte.
    Si is_strict=True, aucun événement hors période n'est retourné.
    """
    seen, events = set(), []
    for doc in docs:
        text = doc.page_content
        d = re.search(r"Date\s*:\s*(\d{2}/\d{2}/\d{4})", text)
        n = re.search(r"[EÉ]v[eé]nement\s*:\s*(.*?)\.", text, re.IGNORECASE)
        l = re.search(r"Lieu\s*:\s*(.*?)\.", text)
        if not d or not n:
            continue
        try:
            ev_date = datetime.strptime(d.group(1), "%d/%m/%Y")
        except ValueError:
            continue
        # Filtre date STRICT
        if not (date_debut.date() <= ev_date.date() <= date_fin.date()):
            continue
        # Filtre ville
        lieu = l.group(1).strip() if l else ""
        if ville_filter and ville_filter.lower() not in lieu.lower():
            continue
        line = f"{n.group(1).strip()} — {d.group(1)} — {lieu}"
        if line not in seen:
            seen.add(line)
            events.append(line)
    return events


def build_prompt(question, events, ville, memory, label_periode=None, is_strict=True):
    history_text = ""
    for msg in memory[-10:]:
        role = "Utilisateur" if msg["role"] == "user" else "Assistant"
        history_text += f"{role} : {msg['content']}\n"
    contexte = "\n".join(events[:10])
    periode_info = f" pour **{label_periode}**" if label_periode else ""

    # Instruction stricte selon le mode
    if is_strict and label_periode:
        instruction_periode = (
            f"IMPORTANT : Réponds UNIQUEMENT avec des événements de {label_periode}. "
            f"N'invente AUCUN événement. N'ajoute AUCUNE suggestion hors de cette période. "
            f"Si la liste est vide, réponds simplement : Aucun événement trouvé à {ville} pour {label_periode}."
        )
    else:
        instruction_periode = (
            f"Utilise les événements de la liste ci-dessous. N'invente rien. "
            f"Si aucun ne correspond, dis-le clairement."
        )

    prompt = (
        f"[INST] Tu es l'assistant Puls-Events, expert en événements culturels à {ville}.\n"
        f"Réponds en français, de façon naturelle et concise.\n"
        f"{instruction_periode}\n"
        f"N'affiche PAS de fenêtre temporelle, pas de recommandations de sites externes.\n"
    )
    if history_text:
        prompt += f"\nHISTORIQUE :\n{history_text}\n"
    prompt += f"\nÉVÉNEMENTS ({ville}{periode_info}) :\n{contexte}\n\nQUESTION : {question} [/INST]"
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

    try:
        environ   = getattr(cl.context.session, "environ", {})
        client_ip = (
            environ.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or
            environ.get("HTTP_X_REAL_IP", "") or
            environ.get("REMOTE_ADDR", "") or None
        )
        if client_ip in ("127.0.0.1", "::1", ""):
            client_ip = None
    except Exception:
        client_ip = None

    logger.info(f"GeoIP: ip={client_ip}, city=Paris")
    ville_detectee = await cl.make_async(detect_city_from_ip)(client_ip)
    init_session(ville_detectee)
    cl.user_session.set("thread_titled", False)

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

    actions = [
        cl.Action(name="set_ville", payload={"v": v}, label=v)
        for v in ["Paris", "Lyon", "Marseille", "Toulouse", "Nice", "Bordeaux", "Lille"]
    ]

    await cl.Message(
        content=(
            f"🎉 **Bienvenue sur Puls-Events, {nom} !**\n\n"
            "Recherchez des événements culturels sur **2025, 2026 et 2027**.\n\n"
            f"📍 Ville actuelle : **{ville_detectee}** — changez via les boutons ou ⚙️.\n"
            "💬 Vos conversations précédentes sont dans la **sidebar à gauche**.\n\n"
            "💡 Exemples : *concerts en août 2026*, *expositions ce week-end*, *spectacles cet été à Lyon*"
        ),
        actions=actions,
    ).send()


# ──────────────────────────────────────────────────
# CALLBACKS
# ──────────────────────────────────────────────────
@cl.action_callback("set_ville")
async def on_ville_selected(action: cl.Action):
    ville = action.payload.get("v", "Paris")
    cl.user_session.set("ville", ville)
    await cl.Message(content=f"📍 Ville sélectionnée : **{ville}**. Posez votre question !").send()


@cl.on_settings_update
async def on_settings_update(settings):
    ville     = settings.get("ville_select", "Paris")
    use_agent = settings.get("use_agent", True)
    cl.user_session.set("ville",     ville)
    cl.user_session.set("use_agent", use_agent)
    await cl.Message(content=f"📍 Ville mise à jour : **{ville}**").send()


@cl.on_chat_resume
async def on_chat_resume(thread):
    init_session("Paris")
    memory = []
    steps = thread.get("steps", []) if isinstance(thread, dict) else getattr(thread, "steps", [])
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
            memory.append({"role": "user",      "content": step_output})
        elif step_type == "assistant_message":
            memory.append({"role": "assistant", "content": step_output})
    cl.user_session.set("memory", memory[-20:])
    logger.info(f"[resume] {len(memory)} messages rechargés")


# ──────────────────────────────────────────────────
# MESSAGE PRINCIPAL
# ──────────────────────────────────────────────────
@cl.on_message
async def on_message(message: cl.Message):

    if message.content.startswith("__GPS__"):
        try:
            coords    = message.content.replace("__GPS__", "").split(",")
            ville_gps = detect_city_from_gps(float(coords[0]), float(coords[1]))
            cl.user_session.set("ville", ville_gps)
            await cl.Message(content=f"📍 Ville détectée : **{ville_gps}**").send()
        except Exception:
            await cl.Message(content="❌ Format incorrect. Exemple : `__GPS__48.8566,2.3522`").send()
        return

    question      = message.content
    ville_session = cl.user_session.get("ville", "Paris")
    vector_db     = cl.user_session.get("vector_db")
    llm           = cl.user_session.get("llm")
    memory        = cl.user_session.get("memory", [])
    use_agent     = cl.user_session.get("use_agent", True)
    start_time    = datetime.now()

    # Nommer la conversation
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

    # Détection ville
    if is_near_me(question) and not any(v.lower() in question.lower() for v in VILLES_FRANCE):
        ville = ville_session
    else:
        ville = detect_ville_in_question(question, ville_session)
        if ville != ville_session:
            await cl.Message(content=f"📍 Ville détectée : **{ville}**").send()
            cl.user_session.set("ville", ville)

    # Détection plage de dates
    date_debut, date_fin, label_periode, is_strict = detect_date_range(question)
    logger.info(f"Plage: {date_debut.date()} → {date_fin.date()} | label={label_periode} | strict={is_strict}")

    # ── Décision : Qdrant ou Web directement ? ──
    # Si ville hors Qdrant → web direct sans passer par Qdrant
    force_web = not ville_dans_qdrant(ville)

    final_msg = cl.Message(content="")
    await final_msg.send()
    fallback_activated = False
    response_text      = ""
    events             = []

    if force_web and use_agent:
        # Ville hors Paris → web directement, silencieusement
        fallback_activated = True
        async with cl.Step(name="🌐 Recherche Web") as step:
            periode_query = label_periode or f"{MOIS_NOMS[date_debut.month]} {date_debut.year}"
            q_enrichie    = f"{question} {periode_query} {ville}"
            saisons = ["été","automne","hiver","printemps","week-end","semaine","demain","hier","aujourd"]
            is_saison = label_periode and any(s in label_periode for s in saisons)
            mois_str  = None if is_saison else f"{date_debut.month:02d}"
            annee_str = str(date_debut.year)
            web_results   = await cl.make_async(search_events_web)(
                q_enrichie, ville=ville, timeout=45,
                mois=mois_str, annee=annee_str,
                date_debut=date_debut, date_fin=date_fin,
                label_periode=label_periode,
            )
            step.output = "Résultats web récupérés"
        response_text = web_results if web_results else (
            f"Aucun événement trouvé à **{ville}**"
            + (f" pour {label_periode}" if label_periode else "") + "."
        )
        await simulate_streaming(response_text, final_msg)

    else:
        # ── Étape 1 : Recherche Qdrant (Paris uniquement) ──
        all_docs = vector_db.similarity_search(question, k=200)
        ville_filter = ville.strip() if ville.strip() else None
        events = filter_events(all_docs, date_debut, date_fin, ville_filter, is_strict)
        # Afficher le Step Qdrant SEULEMENT si des résultats trouvés
        if events:
            qdrant_step = cl.Step(name="🗄️ Qdrant — base locale")
            await qdrant_step.__aenter__()
            qdrant_step.output = f"{len(events)} événements trouvés ({label_periode or 'fenêtre large'})"
            await qdrant_step.__aexit__(None, None, None)

        # ── Étape 2 : Fallback Web ou Génération Mistral ──
        if len(events) < FAISS_MIN_RESULTS and use_agent:
            fallback_activated = True
            async with cl.Step(name="🌐 Recherche Web") as step:
                periode_query = label_periode or f"{MOIS_NOMS[date_debut.month]} {date_debut.year}"
                q_enrichie    = f"{question} {periode_query} {ville}"
                # Si label_periode contient une saison → ne pas passer mois (évite de limiter à un seul mois)
                saisons = ["été","automne","hiver","printemps","week-end","semaine","demain","hier","aujourd"]
                is_saison = label_periode and any(s in label_periode for s in saisons)
                mois_str  = None if is_saison else f"{date_debut.month:02d}"
                annee_str = str(date_debut.year)
                web_results   = await cl.make_async(search_events_web)(
                    q_enrichie, ville=ville, timeout=45,
                    mois=mois_str, annee=annee_str,
                    date_debut=date_debut, date_fin=date_fin,
                    label_periode=label_periode,
                )
                step.output = "Résultats web récupérés"
            response_text = web_results if web_results else (
                f"Aucun événement trouvé à **{ville}**"
                + (f" pour {label_periode}" if label_periode else "") + "."
            )
            await simulate_streaming(response_text, final_msg)

        elif len(events) == 0 and is_strict:
            # Période stricte + 0 résultat + agent désactivé
            response_text = (
                f"Aucun événement trouvé à **{ville}**"
                + (f" pour **{label_periode}**" if label_periode else "") + "."
            )
            await simulate_streaming(response_text, final_msg)

        else:
            async with cl.Step(name="✍️ Génération Mistral") as step:
                prompt = build_prompt(question, events, ville, memory, label_periode, is_strict)
                async for chunk in llm.astream(prompt):
                    token = chunk.content
                    if token:
                        response_text += token
                        await final_msg.stream_token(token)
                step.output = "Réponse générée"

    await final_msg.update()

    # Méta-infos
    duration_ms  = (datetime.now() - start_time).total_seconds() * 1000
    source_label = "🌐 Web" if fallback_activated else f"🗄️ Qdrant ({len(events)})"
    periode_label = f" · 📅 {label_periode}" if label_periode else ""
    await cl.Message(
        content=f"`⏱️ {duration_ms:.0f} ms` · {source_label} · 📍 **{ville}**{periode_label}"
    ).send()

    if events and not fallback_activated:
        sources = "\n".join([f"• {ev}" for ev in events[:5]])
        await cl.Message(content=f"📋 **Sources :**\n{sources}").send()

    # Mise à jour mémoire
    memory.append({"role": "user",      "content": question})
    memory.append({"role": "assistant", "content": response_text})
    cl.user_session.set("memory", memory[-20:])