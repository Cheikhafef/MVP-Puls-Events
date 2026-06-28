import os, logging, threading
from datetime import datetime, timedelta
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

MISTRAL_API_KEY     = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL       = os.getenv("MISTRAL_MODEL", "open-mistral-7b")
MISTRAL_TEMPERATURE = float(os.getenv("MISTRAL_TEMPERATURE", 0.1))

MOIS_NOMS = {
    "01":"janvier","02":"février","03":"mars","04":"avril","05":"mai","06":"juin",
    "07":"juillet","08":"août","09":"septembre","10":"octobre","11":"novembre","12":"décembre"
}

def search_events_web(question, ville="Paris", timeout=45,
                      mois=None, annee=None,
                      date_debut=None, date_fin=None, label_periode=None):
    """
    Recherche web d'événements.
    - label_periode : texte lisible (ex: "demain 28/06/2026", "l'été 2026")
    - date_debut/date_fin : objets datetime pour filtre strict
    - mois/annee : fallback si pas de label
    """
    from smolagents import DuckDuckGoSearchTool
    from langchain_mistralai import ChatMistralAI

    if not MISTRAL_API_KEY:
        logger.error("MISTRAL_API_KEY manquante.")
        return None

    # ── Construire le filtre de période ──
    if label_periode:
        # Période précise avec label lisible
        periode_str = label_periode
        instruction_stricte = (
            f"IMPORTANT : Liste UNIQUEMENT les événements de {label_periode}. "
            f"N'affiche AUCUN événement hors de cette période. "
            f"Pas de 'proches', pas de 'à confirmer' hors période."
        )
        if date_debut and date_fin:
            instruction_stricte += (
                f" Dates strictes : du {date_debut.strftime('%d/%m/%Y')} "
                f"au {date_fin.strftime('%d/%m/%Y')}."
            )
    elif mois and annee:
        periode_str = f"{MOIS_NOMS.get(mois, mois)} {annee}"
        instruction_stricte = (
            f"IMPORTANT : Liste UNIQUEMENT les événements de {periode_str}. "
            f"N'affiche AUCUN événement d'un autre mois."
        )
    elif annee:
        periode_str = f"l'année {annee}"
        instruction_stricte = (
            f"IMPORTANT : Liste UNIQUEMENT les événements de {annee}. "
            f"N'affiche aucun événement hors de {annee}."
        )
    elif mois:
        periode_str = MOIS_NOMS.get(mois, mois)
        instruction_stricte = (
            f"IMPORTANT : Liste UNIQUEMENT les événements de {periode_str}. "
            f"N'affiche aucun événement d'un autre mois."
        )
    else:
        periode_str = "prochains mois"
        instruction_stricte = "Liste les événements à venir les plus pertinents."

    result_container = {"result": None, "error": None}

    def run_search():
        try:
            sites = (
                "site:infolocale.fr OR site:billetreduc.com OR "
                "site:fnacspectacles.com OR site:ticketmaster.fr OR "
                "site:sortir.com OR site:agendaculturel.fr"
            )
            # Construire la requête DuckDuckGo avec période précise
            if label_periode and any(s in label_periode for s in ["été","automne","hiver","printemps"]):
                # Saison : inclure les mois concernés dans la requête
                saison_mois = {
                    "été": "juin juillet août 2026",
                    "automne": "septembre octobre novembre 2026",
                    "hiver": "décembre janvier février",
                    "printemps": "mars avril mai 2026",
                }
                periode_ddg = next((v for k,v in saison_mois.items() if k in label_periode), annee or "")
                requete_ddg = f"{sites} {ville} concerts evenements {periode_ddg}"
            elif label_periode and any(s in label_periode for s in ["demain","hier","aujourd"]):
                # Jour précis : chercher par date exacte
                requete_ddg = f"{sites} {ville} evenements {label_periode} {annee or ''}"
            else:
                requete_ddg = f"{sites} {ville} {question} {annee or ''} {mois or ''}"
            logger.info(f"Requete DuckDuckGo : '{requete_ddg}'")

            tool = DuckDuckGoSearchTool()
            resultats_bruts = tool(requete_ddg)

            if not resultats_bruts:
                result_container["result"] = (
                    f"Aucun résultat trouvé sur le web pour {ville} "
                    f"({periode_str})."
                )
                return

            llm = ChatMistralAI(
                model=MISTRAL_MODEL,
                api_key=MISTRAL_API_KEY,
                temperature=MISTRAL_TEMPERATURE
            )

            prompt = (
                f"[INST] Tu es l'assistant Puls-Events.\n"
                f"Voici des données brutes internet pour {ville} :\n\n"
                f"{resultats_bruts}\n\n"
                f"RÈGLES ABSOLUES :\n"
                f"1. Ville : {ville.upper()} UNIQUEMENT. "
                f"Ignore tout événement d'une autre ville.\n"
                f"2. {instruction_stricte}\n"
                f"3. Pour chaque événement : Nom, Date exacte, Lieu.\n"
                f"4. N'affiche PAS de fenêtre temporelle, pas de recommandations "
                f"de sites, pas de remarques générales.\n"
                f"5. Si aucun événement ne correspond EXACTEMENT à la période "
                f"demandée, réponds : "
                f"Aucun événement trouvé à {ville} pour {periode_str}.\n"
                f"Réponds directement en français. [/INST]"
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