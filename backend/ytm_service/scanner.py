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
