import os, logging, threading
from datetime import datetime, timedelta
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

MISTRAL_API_KEY     = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL       = os.getenv("MISTRAL_MODEL", "open-mistral-7b")
MISTRAL_TEMPERATURE = float(os.getenv("MISTRAL_TEMPERATURE", 0.1))

def search_events_web(question, ville="Paris", timeout=20, mois=None, annee=None):
    from smolagents import DuckDuckGoSearchTool
    from langchain_mistralai import ChatMistralAI
    if not MISTRAL_API_KEY:
        logger.error("MISTRAL_API_KEY manquante.")
        return None

    now = datetime.now()
    date_debut_str = now.replace(year=now.year-1).strftime("%d/%m/%Y")
    date_fin_str   = now.replace(year=now.year+2).strftime("%d/%m/%Y")

    # Filtre strict si mois ou annee demandes
    periode_str = ""
    if mois and annee:
        mois_noms = {"01":"janvier","02":"fevrier","03":"mars","04":"avril","05":"mai","06":"juin","07":"juillet","08":"aout","09":"septembre","10":"octobre","11":"novembre","12":"decembre"}
        periode_str = f"en {mois_noms.get(mois, mois)} {annee} UNIQUEMENT"
    elif annee:
        periode_str = f"en {annee} UNIQUEMENT"
    elif mois:
        mois_noms = {"01":"janvier","02":"fevrier","03":"mars","04":"avril","05":"mai","06":"juin","07":"juillet","08":"aout","09":"septembre","10":"octobre","11":"novembre","12":"decembre"}
        periode_str = f"en {mois_noms.get(mois, mois)} UNIQUEMENT"

    result_container = {"result": None, "error": None}

    def run_search():
        try:
            sites = "site:infolocale.fr OR site:billetreduc.com OR site:fnacspectacles.com OR site:ticketmaster.fr OR site:sortir.com OR site:agendaculturel.fr"
            requete_ddg = f"{sites} {ville} {question} {annee or ''}"
            logger.info(f"Requete DuckDuckGo : '{requete_ddg}'")

            tool = DuckDuckGoSearchTool()
            resultats_bruts = tool(requete_ddg)

            if not resultats_bruts:
                result_container["result"] = f"Aucun resultat trouve sur le web pour {ville}."
                return

            llm = ChatMistralAI(model=MISTRAL_MODEL, api_key=MISTRAL_API_KEY, temperature=MISTRAL_TEMPERATURE)

            filtre_info = f"Privilege les evenements de {periode_str} mais liste aussi les evenements proches si peu de resultats." if periode_str else f"Fenetre : {date_debut_str} au {date_fin_str}."

            prompt = (
                f"[INST] Tu es l'assistant Puls-Events.\n"
                f"Voici des donnees brutes internet pour {ville} :\n\n{resultats_bruts}\n\n"
                f"REGLES ABSOLUES :\n"
                f"1. Ville : {ville.upper()} UNIQUEMENT.\n"
                f"2. {filtre_info}\n"
                f"3. Liste UNIQUEMENT les evenements qui correspondent. Nom, Date, Lieu.\n"
                f"4. N'affiche PAS de fenetre temporelle, pas de recommandations de sites, pas de remarques.\n"
                f"5. Si aucun evenement ne correspond, dis : Aucun evenement trouve a {ville} pour cette periode.\n"
                f"Reponds directement en francais. [/INST]"
            )

            response = llm.invoke(prompt)
            result_container["result"] = response.content

        except Exception as e:
            result_container["error"] = str(e)

    thread = threading.Thread(target=run_search)
    thread.daemon = True
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        logger.warning(f"Timeout {timeout}s pour '{question}' a {ville}")
        return None
    if result_container["error"]:
        logger.error(f"Erreur recherche web : {result_container['error']}")
        return None
    logger.info(f"Recherche web reussie pour '{question}' a {ville}")
    return result_container["result"]


