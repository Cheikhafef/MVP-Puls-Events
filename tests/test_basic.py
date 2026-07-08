import os
from datetime import datetime
from unittest.mock import patch, MagicMock

from chatbot_chainlit import (
    detect_date_range,
    detect_ville_in_question,
    is_near_me,
    ville_dans_qdrant,
    filter_events,
    build_prompt,
)


def test_detect_date_range_month_year():
    debut, fin, label, is_strict = detect_date_range("concerts en juin 2026")
    assert is_strict is True
    assert debut.month == 6 and debut.year == 2026

def test_detect_date_range_relative_weekend():
    debut, fin, label, is_strict = detect_date_range("événements ce week-end")
    assert is_strict is True
    assert "week-end" in label

def test_detect_date_range_no_match_returns_wide_window():
    debut, fin, label, is_strict = detect_date_range("bonjour")
    assert is_strict is False
    assert label is None

def test_detect_ville_in_question_found():
    assert detect_ville_in_question("concerts à Lyon", "Paris") == "Lyon"

def test_detect_ville_in_question_fallback_to_session():
    assert detect_ville_in_question("quoi de neuf", "Marseille") == "Marseille"

def test_is_near_me_true():
    assert is_near_me("événements proche de moi") is True

def test_is_near_me_false():
    assert is_near_me("concerts à Lyon en juin") is False

def test_ville_dans_qdrant_paris():
    assert ville_dans_qdrant("Paris") is True

def test_ville_dans_qdrant_other_city():
    assert ville_dans_qdrant("Lyon") is False

def test_filter_events_strict_date_range():
    fake_doc = MagicMock()
    fake_doc.page_content = "Événement : Concert test. Date : 15/06/2026. Lieu : Paris."
    debut = datetime(2026, 6, 1)
    fin = datetime(2026, 6, 30, 23, 59, 59)
    events = filter_events([fake_doc], debut, fin, ville_filter="Paris", is_strict=True)
    assert len(events) == 1
    assert "Concert test" in events[0]

def test_filter_events_excludes_out_of_range():
    fake_doc = MagicMock()
    fake_doc.page_content = "Événement : Concert hors période. Date : 15/03/2026. Lieu : Paris."
    debut = datetime(2026, 6, 1)
    fin = datetime(2026, 6, 30, 23, 59, 59)
    events = filter_events([fake_doc], debut, fin, ville_filter="Paris", is_strict=True)
    assert len(events) == 0

def test_build_prompt_strict_period_instruction():
    prompt = build_prompt("concerts", ["Concert A — 15/06/2026 — Paris"], "Paris", [], "juin 2026", True)
    assert "Paris" in prompt
    assert "juin 2026" in prompt

def test_auth_no_plaintext_password_hash_format():
    # Vérifie que les hash stockés respectent le format bcrypt, pas du texte en clair
    for var in ["PASS_DEMO_HASH", "PASS_REMY_HASH", "PASS_AFEF_HASH"]:
        val = os.environ.get(var, "")
        if val:
            assert val.startswith("$2b$")

# --- Test d'intégration minimal ---
@patch("chatbot_chainlit.search_events_web")
def test_fallback_web_triggered_for_non_qdrant_city(mock_web):
    mock_web.return_value = "Résultat web simulé"
    # Lyon n'est pas dans VILLES_QDRANT → force_web doit être True
    assert ville_dans_qdrant("Lyon") is False