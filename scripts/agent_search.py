"""
agent_search.py — Sprint 2/3 : Recherche web directe
======================================================
Fallback web temps reel quand FAISS retourne < 2 resultats.

Architecture :
    1. DuckDuckGo appele directement par Python (ville forcee dans la requete)
    2. Sites fiables cibles : infolocale.fr, ticketmaster.fr, fnacspectacles.com...
    3. Mistral-7B formate les resultats bruts (pas d'agent autonome)

Fenetres temporelles : -12 mois / +12 mois (consigne encadreur)
"""

import os
import logging
import threading
from datetime import datetime, timedelta
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

MISTRAL_API_KEY     = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL       = os.getenv("MISTRAL_MODEL", "open-mistral-7b")
MISTRAL_TEMPERATURE = float(os.getenv("MISTRAL_TEMPERATURE", 0.1))


def search_events_web(question, ville="Paris", timeout=20):
    """
    Recherche des evenements en temps reel via DuckDuckGo + Mistral.

    Etape 1 : DuckDuckGo appele directement avec requete incluant la ville
              et des operateurs site: pour cibler les agendas fiables.
    Etape 2 : Mistral formate les resultats bruts en reponse structuree.

    Args:
        question (str) : Question ou critere de recherche de l'utilisateur.
        ville    (str) : Ville cible — forcee dans la requete DuckDuckGo.
        timeout  (int) : Delai max en secondes avant retour None.

    Returns:
        str | None : Reponse formatee, ou None si timeout/erreur.
    """
    from smolagents import DuckDuckGoSearchTool
    from langchain_mistralai import ChatMistralAI

    if not MISTRAL_API_KEY:
        logger.error("MISTRAL_API_KEY manquante.")
        return None

    # Calcul fenetre temporelle -12 mois / +12 mois
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
            # Etape 1 : Requete DuckDuckGo avec ville + sites cibles
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
                result_container["result"] = (
                    "Aucun resultat trouve sur le web pour " + ville + "."
                )
                return

            # Etape 2 : Mistral formate les resultats bruts
            llm = ChatMistralAI(
                model=MISTRAL_MODEL,
                api_key=MISTRAL_API_KEY,
                temperature=MISTRAL_TEMPERATURE
            )

            prompt = (
                "[INST] Tu es l'assistant Puls-Events.\n"
                "Voici des donnees brutes issues d'internet pour " + ville + " :\n\n"
                + resultats_bruts + "\n\n"
                "INSTRUCTIONS :\n"
                "1. Ville demandee : " + ville.upper() + " UNIQUEMENT. "
                "Ignore toute autre ville.\n"
                "2. Fenetre autorisee : entre le " + date_debut_str
                + " et le " + date_fin_str + ".\n"
                "   Tu peux lister des evenements passes recents ET futurs prevus.\n"
                "3. Pour chaque evenement valide : Nom, Date precise, Lieu.\n"
                "4. Si aucun evenement ne correspond, reponds : "
                "'Aucun evenement trouve a " + ville + " pour cette periode.'\n"
                "Reponds en francais, de facon claire et concise. [/INST]"
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
        logger.warning(
            "Timeout " + str(timeout) + "s pour : '"
            + question + "' a " + ville
        )
        return None

    if result_container["error"]:
        logger.error("Erreur recherche web : " + result_container["error"])
        return None

    logger.info(
        "Recherche web reussie pour '" + question + "' a " + ville
    )
    return result_container["result"]