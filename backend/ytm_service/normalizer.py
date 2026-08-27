import re
import hashlib
from typing import Optional

def normalize_text(text: Optional[str]) -> str:
    """Normalize string by lowercasing, stripping whitespace, special chars and accents."""
    if not text:
        return ""
    # Lowercase
    s = text.lower().strip()
    
    # Remove release tags like [Remastered], (Deluxe Version), (Live), etc.
    s = re.sub(r"[\(\[\{].*?(?:remaster|deluxe|version|edition|live|explicit|clean|radio edit|mono|stereo|bonus|expanded|anniversary|feat|ft\.).*?[\)\]\}]", "", s, flags=re.IGNORECASE)
    
    # Remove 'feat.' or 'ft.' and following artists from titles
    s = re.sub(r"\b(?:feat\.?|ft\.?)\b.*$", "", s, flags=re.IGNORECASE)
    
    # Replace punctuation / special characters with space
    s = re.sub(r"[^\w\s]", " ", s)
    
    # Normalize multiple spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s

def parse_duration(duration_str: Optional[str | int | float]) -> Optional[float]:
    """Parse string duration like '3:45' or '1:02:30' or float/int seconds into seconds."""
    if duration_str is None:
        return None
    if isinstance(duration_str, (int, float)):
        return float(duration_str)
    
    parts = str(duration_str).strip().split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    return None

def compute_file_hash(filepath: str, chunk_size: int = 65536) -> str:
    """Compute SHA-256 hash of a file."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(chunk_size):
            sha.update(chunk)
    return sha.hexdigest()

def compute_metadata_hash(artist: Optional[str], album: Optional[str], title: Optional[str], duration: Optional[float]) -> str:
    """Compute deterministic hash of normalized metadata."""
    raw = f"{normalize_text(artist)}|{normalize_text(album)}|{normalize_text(title)}|{round(duration or 0)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
