# Puls-Events — Chatbot RAG Hybride (MVP en production) <br>

Assistant intelligent de recommandation d'événements culturels français.
Architecture **RAG hybride + Agent Web** combinant **Qdrant Cloud**, **Chainlit**, **Mistral-7B** et **smolagents (Hugging Face)**, déployée sur **Azure Container Apps**.

> Projet réalisé dans le cadre d'une formation Data Engineer  — Puls-Events (Projet 13)

**URL production :** https://puls-events-app.purplepebble-68cea5a4.francecentral.azurecontainerapps.io/

---

## Évolution : POC vers MVP <br>

| Fonctionnalité | POC (v1) | MVP (v2 — production) |
|---|---|---|
| Interface | Streamlit (bouton) | Chainlit (chat multi-tours, auth, historique) |
| Base vectorielle | FAISS local | Qdrant Cloud (1786 vecteurs, dim=384) |
| Mémoire | Aucune | Conversationnelle persistante (Supabase PostgreSQL) |
| Géographie | Paris en dur | Base Paris (Qdrant Cloud) + Fallback Agent Web pour le reste des villes françaises|
| Authentification | Aucune | username + mot de passe, multi-utilisateurs |
| Sources web | Aucune | smolagents + DuckDuckGo (fallback si < 2 résultats) |
| Fenêtre temporelle | 12 mois passés | 2025, 2026, 2027 (filtres stricts mois + année + saisons) |
| Déploiement | Local uniquement | Azure Container Apps (Docker, HTTPS natif, auto-scaling) |
| Personnalisation UI | Aucune | Thème custom (CSS), branding Chainlit retiré |

---

## Description <br>

Ce projet est développé pour **Puls-Events**, une plateforme de découverte d'événements culturels en France.

Le système MVP :
- Collecte les événements via l'API Open Agenda
- Indexe les embeddings dans **Qdrant Cloud** (HuggingFace MiniLM-L6-v2, dim=384, 1786 vecteurs) — **Paris uniquement**
- génère des réponses naturelles via **Mistral-7B** (open-mistral-7b)
- Bascule automatiquement vers **smolagents** (DuckDuckGo) si Qdrant retourne moins de 2 résultats
- Pour les 36 villes hors Paris : bascule **directement** vers la recherche web (Qdrant ne couvre que Paris ; ces villes ne sont pas indexées dans la base vectorielle)
- Retient l'**historique conversationnel complet** via Supabase PostgreSQL (5 tables) — stockage des échanges et reprise de conversation ; ne constitue pas encore un profil utilisateur personnalisé (voir Roadmap)
- Détecte la **ville automatiquement** via GeoIP (ipapi.co) + détection dans le texte
- Applique des **filtres temporels stricts** (mois + année, saisons) **et relatifs** (demain, hier, ce week-end) — les deux types de filtres sont livrés et stabilisés en production
- Gère l'**authentification multi-utilisateurs** (username + mot de passe hashé)
- Est déployé en **production** sur Azure Container Apps avec HTTPS natif

---

## Structure du projet <br>

```
puls-events-mvp/
|
|-- .env                        <- Fichier local (non versionné, jamais commit)
|-- .env.example                <- NOUVEAU : exemple sans secrets, à copier en .env
|-- .dockerignore
|-- .gitignore
|-- Dockerfile
|-- requirements.txt            <- Dépendances avec versions figées
|-- README.md
|
|-- chatbot_chainlit.py         <- Application principale (interface, auth, RAG, historique)
|-- agent_search.py             <- Module fallback web (smolagents + DuckDuckGo)
|-- build_vector_db.py          <- Indexation des embeddings dans Qdrant
|-- fetch_events.py             <- Collecte des événements via l'API Open Agenda
|-- migrate_to_qdrant.py        <- Migration des données locales vers Qdrant Cloud
|-- chainlit.py                 <- Script de configuration ou test local de Chainlit
|
|-- tests/                      <- NOUVEAU : tests unitaires + intégration (pytest)
|   `-- test_basic.py
|
|-- data/                       <- Données sources (embeddings, fichiers d'indexation)
|-- .chainlit/
|   `-- config.toml             <- Configuration Chainlit (UI, thème, fonctionnalités)
|
|-- public/                     <- Thème personnalisé (CSS, branding)
|   `-- custom.css
|
|-- acr_config.json             <- Configuration Azure Container Registry
|-- containerapp_config.json    <- Configuration Azure Container Apps
|-- env_config.json             <- Variables d'environnement Azure

```

---

## Installation <br>

### 1. Cloner le projet <br>

```bash
git clone https://github.com/Cheikhafef/MVP-Puls-Events.git
cd MVP-Puls-Events
```

### 2. Créer l'environnement virtuel <br>

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Installer les dépendances <br>

```bash
pip install -r requirements.txt
```

### 4. Configurer les clés API <br>

Créez un fichier `.env` à la racine :

```
MISTRAL_API_KEY=**********************
OPENAGENDA_API_KEY=**********************
QDRANT_URL=**********************
QDRANT_API_KEY=**********************
SUPABASE_URL=**********************
SUPABASE_KEY=**********************
DATABASE_URL=**********************
MISTRAL_MODEL=open-mistral-7b
MISTRAL_TEMPERATURE=0.1
CHAINLIT_AUTH_SECRET=**********************
PASS_DEMO_HASH=**********
PASS_REMY_HASH=**********
PASS_AFEF_HASH=**********
```

---

## Utilisation <br>

### Lancer l'application en local <br>

```bash
chainlit run chatbot_chainlit.py -w
```

Ouvre sur [http://localhost:8000](http://localhost:8000)

### Déploiement Azure Container Apps <br>

```bash
docker build --no-cache -t pulseventsregistry.azurecr.io/puls-events:latest .
az acr login --name pulseventsregistry
docker push pulseventsregistry.azurecr.io/puls-events:latest
az containerapp update --name puls-events-app --resource-group puls-events-rg --revision-suffix vNN
az containerapp ingress traffic set --name puls-events-app --resource-group puls-events-rg --revision-weight puls-events-app--vNN=100
```

---

## Architecture MVP <br>

```mermaid
flowchart TD
    USR["Utilisateur\nQuestion + Ville"]

    subgraph RAG["Pipeline RAG hybride"]
        direction TB
        QDRANT["Qdrant Cloud MMR k=15\nBase vectorielle managée"]
        COND{">= 2 résultats ?"}
        MISTRAL["Mistral-7B\nGeneration RAG"]
    end

    subgraph AGENT["Fallback smolagents"]
        direction TB
        DDG["DuckDuckGo\nrecherche web temps réel"]
        FORMAT["Mistral-7B\nFormatage résultats"]
    end

    subgraph SUPPORT["Systèmes support"]
        direction LR
        AUTH["Authentification\nusername + mot de passe"]
        MEM["Historique\nSupabase PostgreSQL"]
        GEO["Géolocalisation\nIP + texte + manuel"]
    end

    USR --> AUTH
    AUTH --> QDRANT
    QDRANT --> COND
    COND -->|">= 2 résultats"| MISTRAL
    COND -->|"< 2 résultats"| DDG
    DDG --> FORMAT
    MISTRAL --> MEM
    FORMAT --> MEM
    GEO --> QDRANT

    style RAG fill:#FEF9E7,stroke:#D4AC0D
    style AGENT fill:#FADBD8,stroke:#CB4335
    style SUPPORT fill:#EAFAF1,stroke:#27AE60
```

---

## Stack technique <br>

| Composant | Technologie | Détail |
|---|---|---|
| Langage | Python | 3.11-slim (container) |
| Interface | Chainlit | v2.11.1 |
| Base vectorielle | Qdrant Cloud | 1786 vecteurs, dim=384 (Paris uniquement) |
| Embedding | HuggingFace MiniLM-L6-v2 | Calcul dans le container |
| LLM | Mistral AI API | open-mistral-7b |
| Persistance | Supabase PostgreSQL | 5 tables, 500 MB free tier |
| Recherche web | smolagents + DuckDuckGo | Fallback si < 2 résultats Qdrant |
| Authentification | Chainlit Auth | username + mot de passe hashé (bcrypt) |
| Déploiement | Docker + Azure Container Apps | Auto-scaling 1-10 replicas, HTTPS natif |
| Registry | Azure Container Registry | pulseventsregistry2.azurecr.io |
| Thème UI | CSS custom | Branding Chainlit retiré |
| Tests | pytest | Unitaires (dates, fallback web) + 1 test d'intégration |

---

## Résultats <br>

| Métrique | Valeur |
|---|---|
| Villes couvertes | 37 villes françaises (Paris via Qdrant, autres via fallback web) |
| Fenêtre temporelle | 2025, 2026, 2027 |
| Vecteurs Qdrant indexés | 1 786 |
| Temps de réponse Qdrant | < 2s (temps moyen) |
| Temps de réponse fallback web | < 30s (objectif), 9-14s en moyenne mesurée |
| Révisions déployées | 61 (v1 a v61) |
| Filtres temporels | Mois + année, saisons, demain, hier, ce week-end |

---

## Filtres temporels supportes <br>

| Expression | Exemple | Comportement |
|---|---|---|
| Mois + année | "concerts en août 2026" | Filtre strict : seulement août 2026 |
| Année seule | "événements 2027" | Toute l'année 2027 |
| Saisons | "cet été", "cet hiver" | Plage saisonniere stricte |
| Relatif | "demain", "hier", "ce week-end" | Date exacte calculée |
| Jour précis | "le 29 juillet 2026" | Jour exact uniquement |

---

## Sécurité <br>

- Authentification username + mot de passe, sessions Chainlit
- HTTPS natif (certificat SSL géré par Azure)
- Secrets isolés via `.env`, jamais versionnés
- Historique des conversations filtré par `user_id` (clé étrangère)

**A améliorer :** <br>
- Migration des credentials vers Azure Key Vault
- Mise en place d'un rate limiting
- CI/CD automatisé (actuellement déploiement manuel)
- Formalisation de la politique de rétention RGPD

---

## Prochaines étapes (nice-to-have) <br>

- [ ] Application mobile React Native (GPS natif disponible)
- [ ] Interface d'administration utilisateurs
- [ ] Recommandations personnalisées basées sur l'historique
- [ ] Filtres temporels relatifs avancés ("semaine prochaine", "mois prochain")
- [ ] Enrichissement Qdrant multi-villes (actuellement Paris uniquement)
- [ ] CI/CD automatisé via GitHub Actions
- [ ] Migration des secrets vers Azure Key Vault

---

## Auteure

**Afef Cheikh** — Formation Data Engineer
GitHub : [Cheikhafef](https://github.com/Cheikhafef) · Email : cheikhafef@gmail.com

(https://github.com/Cheikhafef/MVP-Puls-Events)
[![Déployé sur Azure](https://img.shields.io/badge/Deployment-Azure%20Container%20Apps-blue)](https://puls-events-app.purplepebble-68cea5a4.francecentral.azurecontainerapps.io/)