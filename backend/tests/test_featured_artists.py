import pytest
from unittest.mock import patch, AsyncMock
from ytm_service.musicbrainz import MusicBrainzClient

@pytest.mark.asyncio
async def test_search_featured_artists_and_videos():
    mb = MusicBrainzClient()

    # Search with Hard Target - Stressin'
    results = await mb.search("Hard Target - Stressin'", limit=5)
    assert len(results) > 0
    top = results[0]
    assert "Hard Target" in top.artist
    assert "Stressin" in top.title
    assert top.score == 100
    assert top.source == "YouTube Music"

@pytest.mark.asyncio
async def test_search_by_title_with_features_only():
    mb = MusicBrainzClient()

    # Search without knowing the main artist
    results = await mb.search("Stressin' ft. Young CP & Malik Cobb", limit=5)
    assert len(results) > 0
    top = results[0]
    assert "Hard Target" in top.artist
    assert "Stressin" in top.title
    assert top.featured_artists is not None
    assert "Young CP" in top.featured_artists
    assert top.score == 100
