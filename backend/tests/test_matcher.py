import pytest
from ytm_service.matcher import evaluate_match
from ytm_service.models import MatchType

def test_evaluate_match_exact():
    m_type, score = evaluate_match(
        local_artist="Metallica",
        local_album="Master of Puppets",
        local_title="Battery",
        local_duration=312.4,
        ytm_artist="Metallica",
        ytm_album="Master of Puppets",
        ytm_title="Battery",
        ytm_duration=312.0
    )
    assert m_type == MatchType.EXACT
    assert score == 1.0

def test_evaluate_match_case_differences():
    m_type, score = evaluate_match(
        local_artist="ac/dc",
        local_album="BACK IN BLACK",
        local_title="HELLS BELLS",
        local_duration=312.0,
        ytm_artist="AC/DC",
        ytm_album="Back in Black",
        ytm_title="Hells Bells",
        ytm_duration=312.0
    )
    assert m_type == MatchType.EXACT
    assert score == 1.0

def test_evaluate_match_remasters():
    m_type, score = evaluate_match(
        local_artist="Nirvana",
        local_album="Nevermind [20th Anniversary Edition]",
        local_title="Smells Like Teen Spirit (Remastered 2011)",
        local_duration=301.0,
        ytm_artist="Nirvana",
        ytm_album="Nevermind",
        ytm_title="Smells Like Teen Spirit",
        ytm_duration=301.2
    )
    assert m_type == MatchType.EXACT
    assert score == 1.0

def test_evaluate_match_strong_different_album():
    m_type, score = evaluate_match(
        local_artist="Tool",
        local_album="Lateralus",
        local_title="Schism",
        local_duration=406.0,
        ytm_artist="Tool",
        ytm_album="Greatest Hits",
        ytm_title="Schism",
        ytm_duration=406.5
    )
    assert m_type == MatchType.STRONG
    assert score == 0.85

def test_evaluate_match_different_durations_live_version():
    # Live or extended version with major duration mismatch should not match studio track
    m_type, score = evaluate_match(
        local_artist="Pink Floyd",
        local_album="Pulse (Live)",
        local_title="Comfortably Numb",
        local_duration=575.0, # 9:35
        ytm_artist="Pink Floyd",
        ytm_album="The Wall",
        ytm_title="Comfortably Numb",
        ytm_duration=382.0  # 6:22
    )
    assert m_type != MatchType.EXACT
    assert score < 0.85

def test_evaluate_match_weak_similar():
    m_type, score = evaluate_match(
        local_artist="Alice in Chains",
        local_album="Jar of Flies",
        local_title="Nutshell",
        local_duration=259.0,
        ytm_artist="Alice in Chains (Tribute)",
        ytm_album="Tribute Album",
        ytm_title="Nutshell Acoustic",
        ytm_duration=250.0
    )
    assert m_type in (MatchType.WEAK, MatchType.NONE)

def test_evaluate_match_no_match():
    m_type, score = evaluate_match(
        local_artist="Pantera",
        local_album="Vulgar Display of Power",
        local_title="Walk",
        local_duration=314.0,
        ytm_artist="Slayer",
        ytm_album="Reign in Blood",
        ytm_title="Raining Blood",
        ytm_duration=254.0
    )
    assert m_type == MatchType.NONE
    assert score < 0.5
