import os
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Optional, Union, Dict, Any, Tuple
from pydantic import BaseModel
import mutagen

logger = logging.getLogger("ytm_sync.audio_fingerprint")


class AudioFingerprintMismatchError(RuntimeError):
    """Raised when downloaded audio fails duration or acoustic characteristic verification."""
    pass


class AudioCharacteristics(BaseModel):
    duration: float
    codec: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    bitrate: Optional[int] = None
    audio_pcm_hash: Optional[str] = None


def extract_audio_characteristics(file_path: Union[str, Path]) -> AudioCharacteristics:
    """
    Extract duration, codec, sample_rate, channels, bitrate, and a decoded PCM hash from an audio file.
    Uses mutagen for fast metadata extraction and ffmpeg for decoded PCM audio hash.
    """
    path = Path(file_path)
    if not path.exists() or path.stat().st_size == 0:
        raise FileNotFoundError(f"Audio file not found or empty: {path}")

    duration = 0.0
    codec = path.suffix.lstrip(".").lower()
    sample_rate = None
    channels = None
    bitrate = None

    try:
        audio = mutagen.File(str(path))
        if audio and audio.info:
            duration = float(getattr(audio.info, "length", 0.0) or 0.0)
            sample_rate = getattr(audio.info, "sample_rate", None)
            channels = getattr(audio.info, "channels", None)
            bitrate = getattr(audio.info, "bitrate", None)
            if hasattr(audio.info, "codec"):
                codec = str(getattr(audio.info, "codec"))
    except Exception as ex:
        logger.warning(f"Mutagen extraction failed for {path}: {ex}")

    # Fallback to ffprobe if duration or properties are missing
    if (duration == 0.0 or sample_rate is None) and shutil.which("ffprobe"):
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(path)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                import json
                data = json.loads(res.stdout)
                audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
                fmt = data.get("format", {})
                if duration == 0.0:
                    duration = float(fmt.get("duration") or audio_stream.get("duration") or 0.0)
                if sample_rate is None and audio_stream.get("sample_rate"):
                    sample_rate = int(audio_stream["sample_rate"])
                if channels is None and audio_stream.get("channels"):
                    channels = int(audio_stream["channels"])
                if bitrate is None and fmt.get("bit_rate"):
                    bitrate = int(fmt["bit_rate"])
                if not codec and audio_stream.get("codec_name"):
                    codec = audio_stream["codec_name"]
        except Exception as ex:
            logger.warning(f"ffprobe extraction fallback failed for {path}: {ex}")

    # Generate decoded PCM hash using ffmpeg (independent of ID3 tags)
    pcm_hash = None
    if shutil.which("ffmpeg"):
        try:
            cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a", "-f", "md5", "-"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0 and res.stdout.strip():
                # Format is MD5=<hash>
                raw = res.stdout.strip()
                pcm_hash = raw.split("=")[-1] if "=" in raw else raw
        except Exception as ex:
            logger.debug(f"ffmpeg pcm hash calculation failed for {path}: {ex}")

    return AudioCharacteristics(
        duration=round(duration, 2),
        codec=codec,
        sample_rate=sample_rate,
        channels=channels,
        bitrate=bitrate,
        audio_pcm_hash=pcm_hash
    )


def compare_audio_characteristics(
    expected: Union[AudioCharacteristics, Dict[str, Any]],
    actual: Union[AudioCharacteristics, Dict[str, Any]],
    max_duration_diff: float = 4.0
) -> Tuple[bool, str]:
    """
    Compare actual downloaded audio characteristics against expected characteristics.
    Returns (is_valid: bool, reason: str).
    Flags discrepancies such as:
      Expected: 3:47 (227s)
      Downloaded: 4:01 (241s)
    """
    exp_dur = getattr(expected, "duration", None) if not isinstance(expected, dict) else expected.get("duration")
    act_dur = getattr(actual, "duration", None) if not isinstance(actual, dict) else actual.get("duration")

    if exp_dur is not None and act_dur is not None:
        try:
            exp_dur_f = float(exp_dur)
            act_dur_f = float(act_dur)
            diff = abs(act_dur_f - exp_dur_f)
            if diff > max_duration_diff:
                return (
                    False,
                    f"Duration mismatch: expected {exp_dur_f:.1f}s, got {act_dur_f:.1f}s (diff {diff:.1f}s > {max_duration_diff:.1f}s)"
                )
        except (ValueError, TypeError):
            pass

    exp_hash = getattr(expected, "audio_pcm_hash", None) if not isinstance(expected, dict) else expected.get("audio_pcm_hash")
    act_hash = getattr(actual, "audio_pcm_hash", None) if not isinstance(actual, dict) else actual.get("audio_pcm_hash")
    if exp_hash and act_hash and exp_hash != act_hash:
        logger.info(f"Audio PCM hash differs: expected {exp_hash}, got {act_hash}")

    return True, "Audio characteristics verified within tolerance"


def verify_audio_integrity(
    audio_path: Union[str, Path],
    expected_duration: Optional[float] = None,
    expected_hash: Optional[str] = None,
    max_duration_diff: float = 4.0
) -> AudioCharacteristics:
    """
    Extracts audio characteristics of audio_path and validates against expected parameters.
    If validation fails, raises AudioFingerprintMismatchError.
    """
    actual = extract_audio_characteristics(audio_path)

    if expected_duration is not None and expected_duration > 0:
        is_valid, reason = compare_audio_characteristics(
            {"duration": expected_duration, "audio_pcm_hash": expected_hash},
            actual,
            max_duration_diff=max_duration_diff
        )
        if not is_valid:
            logger.error(
                f"AUDIO FINGERPRINT VIOLATION: {reason}\n"
                f"file={audio_path}\n"
                f"expected_duration={expected_duration:.1f}s\n"
                f"actual_duration={actual.duration:.1f}s"
            )
            raise AudioFingerprintMismatchError(reason)

    return actual
