"""
agent_search.py — Sprint 3
Plage temporelle : -12 mois et +12 mois 
Sites ciblés pour eviter les résultats hors contexte.
"""

import os
import logging
import threading
from datetime import datetime, timedelta
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "open-mistral-7b")
MISTRAL_TEMPERATURE = float(os.getenv("MISTRAL_TEMPERATURE", 0.1))


def search_events_web(question, ville="Paris", timeout=20):
    from smolagents import DuckDuckGoSearchTool
    from langchain_mistralai import ChatMistralAI

    if not MISTRAL_API_KEY:
        logger.error("MISTRAL_API_KEY manquante.")
        return None

    now = datetime.now()

    try:
        date_debut_dt = now.replace(year=now.year - 1)
    except ValueError:
        date_debut_dt = now - timedelta(days=365)

    try:
        date_fin_dt = now.replace(year=now.year + 1)
    except ValueError:
        date_fin_dt = now + timedelta(days=365)

    date_debut_str = date_debut_dt.strftime("%d/%m/%Y")
    date_fin_str   = date_fin_dt.strftime("%d/%m/%Y")

    result_container = {"result": None, "error": None}

    def run_search():
        try:
            # Sites fiables — pas de annee_str pour capter futur et passé
            sites_evenements = (
                "site:infolocale.fr OR site:billetreduc.com OR "
                "site:fnacspectacles.com OR site:ticketmaster.fr OR "
                "site:sortir.com OR site:agendaculturel.fr"
            )
            requete_ddg = sites_evenements + " " + ville + " " + question
            logger.info("Requete DuckDuckGo : '" + requete_ddg + "'")

            tool = DuckDuckGoSearchTool()
            resultats_bruts = tool(requete_ddg)

            if not resultats_bruts:
                result_container["result"] = "Aucun résultat trouvé sur le web pour " + ville + "."
                return

            llm = ChatMistralAI(
                model=MISTRAL_MODEL,
                api_key=MISTRAL_API_KEY,
                temperature=MISTRAL_TEMPERATURE
            )

            prompt = (
                "[INST] Tu es l'assistant Puls-Events.\n"
                "Voici des données brutes issues d'internet pour " + ville + " :\n\n"
                + resultats_bruts + "\n\n"
                "INSTRUCTIONS :\n"
                "1. Ville demandée : " + ville.upper() + " UNIQUEMENT. Ignore toute autre ville.\n"
                "2. Fenêtre autorisée : entre le " + date_debut_str + " et le " + date_fin_str + ".\n"
                "   Tu peux lister des événements passés récents ET des événements futurs prévus.\n"
                "3. Pour chaque événement valide : Nom, Date précise, Lieu.\n"
                "4. Si aucun événement ne correspond, réponds : "
                "'Aucun événement trouvé à " + ville + " pour cette période.'\n"
                "Réponds en français, de façon claire et concise. [/INST]"
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
        logger.warning("Timeout " + str(timeout) + "s pour '" + question + "' a " + ville)
        return None

    if result_container["error"]:
        logger.error("Erreur : " + result_container["error"])
        return None

    return result_container["result"]
