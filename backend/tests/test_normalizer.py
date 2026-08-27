import pytest
from ytm_service.normalizer import normalize_text, parse_duration, compute_metadata_hash

def test_normalize_text_case_and_punctuation():
    assert normalize_text("AC/DC") == "ac dc"
    assert normalize_text("Metallica - Master of Puppets") == "metallica master of puppets"

def test_normalize_text_removes_tags():
    assert normalize_text("Battery (Remastered 2016)") == "battery"
    assert normalize_text("Nutshell [Live at the Majestic Theatre]") == "nutshell"
    assert normalize_text("Song Title (Deluxe Edition)") == "song title"
    assert normalize_text("Hit Track feat. Drake") == "hit track"

def test_parse_duration():
    assert parse_duration("3:45") == 225.0
    assert parse_duration("1:02:30") == 3750.0
    assert parse_duration(180) == 180.0
    assert parse_duration("invalid") is None

def test_compute_metadata_hash_consistency():
    h1 = compute_metadata_hash("Tool", "Lateralus", "Schism", 406.0)
    h2 = compute_metadata_hash("TOOL", "Lateralus [Deluxe]", "Schism (Radio Edit)", 406.2)
    assert h1 == h2
