"""Download only DeepSeek's public tokenizer JSON; never model weights/code.

The URL is linked by the provider's official token-usage documentation. ZIP
members are read in memory; only one tokenizer.json is written to a fixed cache.
"""

import hashlib
import io
import json
import stat
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen
from zipfile import ZipFile

SOURCE_URL = "https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip"
DOCUMENTATION_URL = "https://api-docs.deepseek.com/quick_start/token_usage/"
CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "deepseek_v4"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024 * 1024


def install_archive(archive: bytes, destination: Path) -> dict:
    """Install inert JSON from an archive; no ZipFile.extract or executable files."""
    from tokenizers import Tokenizer

    if len(archive) > MAX_ARCHIVE_BYTES:
        raise ValueError("TOKENIZER_ARCHIVE_TOO_LARGE")
    with ZipFile(io.BytesIO(archive)) as bundle:
        candidates = [
            member
            for member in bundle.infolist()
            if not member.is_dir() and PurePosixPath(member.filename).name == "tokenizer.json"
        ]
        if len(candidates) != 1:
            raise ValueError("TOKENIZER_JSON_AMBIGUOUS_OR_MISSING")
        member = candidates[0]
        if member.file_size > MAX_JSON_BYTES or stat.S_ISLNK(member.external_attr >> 16):
            raise ValueError("TOKENIZER_JSON_INVALID")
        payload = bundle.read(member)
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("TOKENIZER_JSON_INVALID")
    # Rust tokenizer JSON parsing does not load Python scripts or remote weights.
    try:
        Tokenizer.from_str(payload.decode("utf-8"))
    except Exception:
        raise ValueError("TOKENIZER_JSON_INVALID") from None
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "tokenizer.json"
    temporary = destination / "tokenizer.json.download"
    temporary.write_bytes(payload)
    temporary.replace(json_path)
    manifest = {
        "encoding": "deepseek_v4",
        "source_url": SOURCE_URL,
        "documentation_url": DOCUMENTATION_URL,
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "tokenizer_sha256": hashlib.sha256(payload).hexdigest(),
        "downloaded_at": datetime.now(UTC).isoformat(),
        "format": "tokenizers-json",
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    request = Request(SOURCE_URL, headers={"User-Agent": "ChessX-public-tokenizer-setup"})
    with urlopen(request, timeout=60) as response:
        if response.geturl() != SOURCE_URL:
            raise ValueError("TOKENIZER_UNEXPECTED_REDIRECT")
        archive = response.read(MAX_ARCHIVE_BYTES + 1)
    manifest = install_archive(archive, CACHE_DIR)
    print(
        json.dumps(
            {
                "encoding": manifest["encoding"],
                "cache": str(CACHE_DIR),
                "tokenizer_sha256": manifest["tokenizer_sha256"],
                "archive_sha256": manifest["archive_sha256"],
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "TOKENIZER_SETUP_FAILED: public tokenizer download or validation failed."
        ) from None
