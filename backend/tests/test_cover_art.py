import pytest
import tempfile
from pathlib import Path
from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from mutagen.easyid3 import EasyID3
from ytm_service.scanner import write_metadata_tags, extract_artwork

def test_cover_art_embedding_mp3():
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b"\xff\xfb\x90\x44" + b"\x00" * 2000)
        p = Path(f.name)

    fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 100

    ok = write_metadata_tags(
        p,
        title="Uptown Girl",
        artist="Billy Joel",
        album="An Innocent Man",
        track_number=6,
        cover_bytes=fake_jpeg,
    )
    assert ok is True

    # Read back and verify ID3 tags and APIC frame
    id3 = ID3(str(p))
    apic_frames = [k for k in id3.keys() if k.startswith("APIC")]
    assert len(apic_frames) == 1
    assert id3[apic_frames[0]].data == fake_jpeg

    # Verify extract_artwork
    extracted = extract_artwork(p)
    assert extracted is not None
    data, mime = extracted
    assert data == fake_jpeg
    assert "jpeg" in mime

    p.unlink()
