import os
import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable
import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

from .config import settings
from .database import db
from .normalizer import compute_file_hash, compute_metadata_hash

logger = logging.getLogger("ytm_sync.scanner")

def extract_metadata(filepath: Path) -> dict:
    """Extract audio metadata using mutagen with fallbacks."""
    stat = filepath.stat()
    ext = filepath.suffix.lower()
    
    metadata = {
        "path": str(filepath.resolve()),
        "filename": filepath.name,
        "artist": None,
        "album": None,
        "title": filepath.stem,
        "track_number": None,
        "disc_number": None,
        "duration": None,
        "format": ext.lstrip(".").upper(),
        "file_size": stat.st_size,
        "modified_time": stat.st_mtime,
    }

    try:
        audio = mutagen.File(str(filepath), easy=True)
        if audio is not None:
            if audio.info and hasattr(audio.info, "length"):
                metadata["duration"] = round(audio.info.length, 2)
            
            # Easy tags extraction
            if "artist" in audio and audio["artist"]:
                metadata["artist"] = audio["artist"][0]
            elif "albumartist" in audio and audio["albumartist"]:
                metadata["artist"] = audio["albumartist"][0]

            if "album" in audio and audio["album"]:
                metadata["album"] = audio["album"][0]

            if "title" in audio and audio["title"]:
                metadata["title"] = audio["title"][0]

            if "tracknumber" in audio and audio["tracknumber"]:
                tr_str = str(audio["tracknumber"][0]).split("/")[0]
                try:
                    metadata["track_number"] = int(tr_str)
                except ValueError:
                    pass

            if "discnumber" in audio and audio["discnumber"]:
                dn_str = str(audio["discnumber"][0]).split("/")[0]
                try:
                    metadata["disc_number"] = int(dn_str)
                except ValueError:
                    pass
    except Exception as e:
        logger.warning(f"Error parsing metadata for {filepath}: {e}")

    # Compute hashes
    try:
        metadata["file_hash"] = compute_file_hash(str(filepath))
    except Exception as e:
        logger.warning(f"Could not compute file hash for {filepath}: {e}")
        metadata["file_hash"] = None

    metadata["metadata_hash"] = compute_metadata_hash(
        metadata["artist"],
        metadata["album"],
        metadata["title"],
        metadata["duration"]
    )
    return metadata

import httpx

def fetch_cover_image_bytes(
    cover_url: Optional[str] = None,
    artist: Optional[str] = None,
    title: Optional[str] = None,
    album: Optional[str] = None,
) -> Optional[bytes]:
    """Fetch cover art image bytes from URL, base64 data URI, local path, or auto-query iTunes for high-res artwork."""
    try:
        if cover_url:
            c_url = cover_url.strip()
            if c_url.startswith("data:image/"):
                import base64
                _, b64_data = c_url.split(",", 1)
                return base64.b64decode(b64_data)
            elif c_url.startswith("http://") or c_url.startswith("https://"):
                with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                    r = client.get(c_url)
                    if r.status_code == 200 and r.content:
                        return r.content
            elif Path(c_url).is_file():
                return Path(c_url).read_bytes()

        # Fallback: Auto-search iTunes by artist + title/album
        terms = []
        if title:
            terms.append(title)
        if artist:
            terms.append(artist)
        if album and not terms:
            terms.append(album)
        term = " ".join(terms).strip()
        if term:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                r = client.get("https://itunes.apple.com/search", params={"term": term, "entity": "song", "limit": 1})
                if r.status_code == 200:
                    items = r.json().get("results", [])
                    if items:
                        art = items[0].get("artworkUrl100", "").replace("100x100bb", "600x600bb")
                        if art:
                            img_res = client.get(art)
                            if img_res.status_code == 200 and img_res.content:
                                return img_res.content
    except Exception as e:
        logger.warning(f"Could not fetch cover art: {e}")
    return None

def extract_artwork(filepath: Path) -> Optional[tuple[bytes, str]]:
    """Extract embedded cover art bytes and mime type from an audio file."""
    try:
        p = Path(filepath)
        if not p.exists():
            return None
        ext = p.suffix.lower()
        if ext == ".mp3":
            from mutagen.id3 import ID3
            id3 = ID3(str(p))
            for k in id3.keys():
                if k.startswith("APIC"):
                    frame = id3[k]
                    return frame.data, frame.mime or "image/jpeg"
        elif ext == ".flac":
            from mutagen.flac import FLAC
            flac = FLAC(str(p))
            if flac.pictures:
                pic = flac.pictures[0]
                return pic.data, pic.mime or "image/jpeg"
        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover
            mp4 = MP4(str(p))
            if "covr" in mp4 and mp4["covr"]:
                covr = mp4["covr"][0]
                mime = "image/png" if getattr(covr, "imageformat", None) == MP4Cover.FORMAT_PNG else "image/jpeg"
                return bytes(covr), mime
    except Exception as e:
        logger.debug(f"Could not extract artwork from {filepath}: {e}")
    return None

def write_metadata_tags(
    filepath: Path,
    title: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    track_number: Optional[int] = None,
    cover_url: Optional[str] = None,
    cover_bytes: Optional[bytes] = None,
) -> bool:
    """Write audio metadata tags and embed album cover art using mutagen."""
    try:
        p = Path(filepath)
        if not p.exists():
            return False
        
        ext = p.suffix.lower()
        if ext == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError
            try:
                audio = EasyID3(str(p))
            except ID3NoHeaderError:
                ID3().save(str(p))
                audio = EasyID3(str(p))
        else:
            audio = mutagen.File(str(p), easy=True)

        if audio is None:
            return False

        if title is not None:
            audio["title"] = [title]
        if artist is not None:
            audio["artist"] = [artist]
        if album is not None:
            audio["album"] = [album]
        if track_number is not None:
            audio["tracknumber"] = [str(track_number)]

        audio.save()

        # Cover Art Embedding
        if cover_bytes is None:
            cover_bytes = fetch_cover_image_bytes(cover_url=cover_url, artist=artist, title=title, album=album)

        if cover_bytes:
            try:
                # If image is webp, convert to jpeg via ffmpeg
                if cover_bytes.startswith(b'RIFF') and b'WEBP' in cover_bytes[:16]:
                    conv = subprocess.run(
                        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "mjpeg", "pipe:1"],
                        input=cover_bytes,
                        capture_output=True
                    )
                    if conv.returncode == 0 and conv.stdout:
                        cover_bytes = conv.stdout

                is_png = cover_bytes.startswith(b'\x89PNG')
                mime_type = "image/png" if is_png else "image/jpeg"

                if ext == ".mp3":
                    from mutagen.id3 import ID3, APIC
                    id3 = ID3(str(p))
                    id3.delall("APIC")
                    id3.add(APIC(
                        encoding=3,
                        mime=mime_type,
                        type=3,  # Front Cover
                        desc="Cover",
                        data=cover_bytes
                    ))
                    id3.save(str(p), v2_version=3)
                    logger.info(f"Embedded cover art ({len(cover_bytes)} bytes) into MP3: {p.name}")
                elif ext == ".flac":
                    from mutagen.flac import FLAC, Picture
                    flac_audio = FLAC(str(p))
                    pic = Picture()
                    pic.type = 3
                    pic.mime = mime_type
                    pic.desc = "Cover"
                    pic.data = cover_bytes
                    flac_audio.clear_pictures()
                    flac_audio.add_picture(pic)
                    flac_audio.save()
                    logger.info(f"Embedded cover art ({len(cover_bytes)} bytes) into FLAC: {p.name}")
                elif ext in (".m4a", ".mp4"):
                    from mutagen.mp4 import MP4, MP4Cover
                    mp4_audio = MP4(str(p))
                    fmt = MP4Cover.FORMAT_PNG if is_png else MP4Cover.FORMAT_JPEG
                    mp4_audio["covr"] = [MP4Cover(cover_bytes, imageformat=fmt)]
                    mp4_audio.save()
                    logger.info(f"Embedded cover art ({len(cover_bytes)} bytes) into M4A: {p.name}")
            except Exception as e:
                logger.warning(f"Could not embed cover art into {filepath}: {e}")

        return True
    except Exception as e:
        logger.warning(f"Could not write metadata tags to {filepath}: {e}")
        return False

class MusicScanner:
    def __init__(self):
        self.is_scanning = False
        self.last_scanned_count = 0
        self.errors: list[str] = []

    async def scan_folders(self, folders: list[str], progress_callback: Optional[Callable[[int, str], None]] = None) -> int:
        """Scan a list of folder paths recursively and insert/update in SQLite."""
        if self.is_scanning:
            logger.info("Scan already in progress.")
            return self.last_scanned_count

        self.is_scanning = True
        self.errors = []
        scanned_count = 0
        
        try:
            for folder_str in folders:
                folder = Path(folder_str)
                if not folder.exists() or not folder.is_dir():
                    logger.warning(f"Folder does not exist or is not a directory: {folder_str}")
                    continue

                for root, _, files in os.walk(folder):
                    for filename in files:
                        ext = Path(filename).suffix.lower()
                        if ext in settings.supported_extensions:
                            file_path = Path(root) / filename
                            try:
                                # Run extraction in threadpool
                                meta = await asyncio.to_thread(extract_metadata, file_path)
                                await db.upsert_music_file(meta)
                                scanned_count += 1
                                if progress_callback:
                                    progress_callback(scanned_count, str(file_path))
                            except Exception as e:
                                err_msg = f"Failed to process {file_path}: {e}"
                                logger.error(err_msg)
                                self.errors.append(err_msg)

            self.last_scanned_count = scanned_count
            return scanned_count
        finally:
            self.is_scanning = False

scanner = MusicScanner()
