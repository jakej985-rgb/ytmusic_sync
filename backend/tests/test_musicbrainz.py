import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from ytm_service.musicbrainz import MusicBrainzClient
from ytm_service.main import app
from httpx import AsyncClient, ASGITransport

@pytest.fixture
def mb_client():
    return MusicBrainzClient()

def test_parse_artist_credits_simple(mb_client):
    credits = [{"name": "Akon", "joinphrase": ""}]
    primary, feat = mb_client._parse_artist_credits(credits)
    assert primary == "Akon"
    assert feat is None

def test_parse_artist_credits_multiple_feat(mb_client):
    credits = [
        {"name": "C-Mob", "joinphrase": " feat. "},
        {"name": "Brotha Lynch Hung", "joinphrase": ", "},
        {"name": "Twisted Insane", "joinphrase": ""}
    ]
    primary, feat = mb_client._parse_artist_credits(credits)
    assert primary == "C-Mob"
    assert feat == "Brotha Lynch Hung, Twisted Insane"

def test_parse_artist_credits_embedded_feat(mb_client):
    credits = [{"name": "C-Mob ft. Brotha Lynch Hung", "joinphrase": ""}]
    primary, feat = mb_client._parse_artist_credits(credits)
    assert primary == "C-Mob"
    assert feat == "Brotha Lynch Hung"

@pytest.mark.asyncio
async def test_search_structured(mb_client):
    mock_payload = {
        "recordings": [
            {
                "id": "mbid-12345",
                "title": "For Some Strange Reason",
                "score": 100,
                "artist-credit": [
                    {"name": "C-Mob", "joinphrase": " feat. "},
                    {"name": "Brotha Lynch Hung", "joinphrase": ""}
                ],
                "releases": [
                    {
                        "title": "Masterpiece of Mind",
                        "date": "2013-05-14",
                        "media": [{"track-offset": 4}]
                    }
                ]
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch.object(mb_client, "_search_ytmusic", new_callable=AsyncMock, return_value=[]), \
         patch.object(mb_client, "_search_deezer", new_callable=AsyncMock, return_value=[]):
        mock_get.return_value = mock_resp
        results = await mb_client.search(artist="C-Mob", title="For Some Strange Reason")

        assert len(results) >= 1
        res = results[0]
        assert res.primary_title == "For Some Strange Reason"
        assert res.artist == "C-Mob"
        assert res.featured_artists == "Brotha Lynch Hung"
        assert res.title == "For Some Strange Reason ft. Brotha Lynch Hung"
        assert res.album == "Masterpiece of Mind"
        assert res.track_number == 5
        assert res.release_date == "2013-05-14"

@pytest.mark.asyncio
async def test_api_musicbrainz_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("ytm_service.main.musicbrainz_client.search", new_callable=AsyncMock) as mock_search:
            from ytm_service.models import MusicBrainzMatch
            mock_search.return_value = [
                MusicBrainzMatch(
                    mbid="mbid-123",
                    title="For Some Strange Reason ft. Brotha Lynch Hung",
                    primary_title="For Some Strange Reason",
                    artist="C-Mob",
                    featured_artists="Brotha Lynch Hung",
                    album="Masterpiece of Mind",
                    track_number=5,
                    release_date="2013-05-14",
                    score=95
                )
            ]

            resp = await client.get("/api/musicbrainz/search?artist=C-Mob&title=For%20Some%20Strange%20Reason")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["artist"] == "C-Mob"
            assert data[0]["title"] == "For Some Strange Reason ft. Brotha Lynch Hung"
            assert data[0]["album"] == "Masterpiece of Mind"

@pytest.mark.asyncio
async def test_provider_filter_selection():
    mb_client = MusicBrainzClient()
    with patch.object(mb_client, "_search_ytmusic", new_callable=AsyncMock, return_value=[]) as mock_ytm, \
         patch.object(mb_client, "_search_deezer", new_callable=AsyncMock, return_value=[]) as mock_deezer, \
         patch.object(mb_client, "_search_itunes", new_callable=AsyncMock, return_value=[]) as mock_itunes, \
         patch.object(mb_client, "_search_musicbrainz", new_callable=AsyncMock, return_value=[]) as mock_mb:

        # 1. Search with provider='deezer'
        await mb_client.search(artist="Test", title="Song", provider="deezer")
        mock_deezer.assert_called_once()
        mock_ytm.assert_not_called()
        mock_itunes.assert_not_called()
        mock_mb.assert_not_called()

        mock_deezer.reset_mock()

        # 2. Search with provider='ytm'
        await mb_client.search(artist="Test", title="Song", provider="ytm")
        mock_ytm.assert_called_once()
        mock_deezer.assert_not_called()
        mock_itunes.assert_not_called()
        mock_mb.assert_not_called()

        mock_ytm.reset_mock()

        # 3. Search with provider='musicbrainz'
        await mb_client.search(artist="Test", title="Song", provider="musicbrainz")
        mock_mb.assert_called_once()
        mock_ytm.assert_not_called()
        mock_deezer.assert_not_called()
        mock_itunes.assert_not_called()
