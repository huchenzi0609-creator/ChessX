"""A confined, versioned library of independent character and scenario files.

Readers and writers share a group lock. Character pairs are staged as complete
directories before publication; interrupted swaps restore the previous pair on
the next access. Local files are private to the operating-system user. Bundled
groups are immutable and never receive lock files or other writes.
"""

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from chessx.domain.models import PieceConfig, Scenario
from chessx.presets.models import CharacterInformation, GroupMetadata, PresetScenario

MAX_MARKDOWN_BYTES = 256 * 1024
MAX_JSON_BYTES = 1024 * 1024
_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
_BACKUP = re.compile(r"\.([a-zA-Z0-9_-]{1,64})\.backup\Z")
_PENDING = re.compile(r"\.[a-zA-Z0-9_-]{1,64}\.pending-[0-9a-f]{32}\Z")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class PresetError(Exception):
    """Safe public errors never include source content or filesystem details."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _identifier(value: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise PresetError("INVALID_ID", "Use 1–64 ASCII letters, digits, underscores or hyphens.")
    return value


def _revision(*data: bytes) -> str:
    digest = hashlib.sha256()
    for part in data:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _json_bytes(value: dict) -> bytes:
    try:
        encoded = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
            "utf-8"
        )
    except (ValueError, TypeError, UnicodeError):
        raise PresetError("INVALID_DATA", "JSON data is invalid.") from None
    if len(encoded) > MAX_JSON_BYTES:
        raise PresetError("INVALID_DATA", "JSON data exceeds the size limit.")
    return encoded


def _model(model: type, value: Any, *, disk: bool = False):
    try:
        return model.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        code = "CORRUPT_DATA" if disk else "INVALID_DATA"
        raise PresetError(code, "Preset data does not match its schema.") from None


def _sync_directory(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_private(path: Path, data: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


class PresetLibrary:
    def __init__(self, bundled_root: Path, local_root: Path):
        self.bundled_root = Path(bundled_root).absolute()
        self.local_root = Path(local_root).absolute()
        for root in (self.bundled_root, self.local_root):
            self._safe_path(root, root)
        if self.bundled_root == self.local_root:
            raise PresetError("UNSAFE_PATH", "Bundled and local roots must be separate.")

    @staticmethod
    def _safe_path(root: Path, path: Path):
        """Validate lexical confinement and every existing child before I/O."""
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise PresetError("UNSAFE_PATH", "Preset paths must stay within the library.") from None
        if any(part in ("..", ".") for part in relative.parts):
            raise PresetError("UNSAFE_PATH", "Preset paths must stay within the library.")
        current = root
        for part in (None, *relative.parts):
            if part is not None:
                current = current / part
            if current.is_symlink():
                raise PresetError("UNSAFE_PATH", "Symbolic links are not allowed in presets.")

    @contextmanager
    def _guard(self, path: Path, *, writable: bool):
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(str(path), threading.RLock())
        with lock:
            if not writable:
                yield
                return
            self._safe_path(self.local_root, path)
            fd = None
            try:
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            except OSError:
                raise PresetError("IO_ERROR", "Preset storage is unavailable.") from None
            finally:
                if fd is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)

    def _read(self, root: Path, path: Path, limit: int) -> bytes:
        self._safe_path(root, path)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                # Reject directories, devices, sockets and FIFOs without blocking.
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise PresetError("CORRUPT_DATA", "Preset files must be regular files.")
                value = stream.read(limit + 1)
        except OSError:
            raise PresetError("CORRUPT_DATA", "A required preset file cannot be read.") from None
        if len(value) > limit:
            raise PresetError("CORRUPT_DATA", "A preset file exceeds the size limit.")
        return value

    def _read_json(self, root: Path, path: Path) -> tuple[Any, bytes]:
        raw = self._read(root, path, MAX_JSON_BYTES)
        try:
            return json.loads(raw), raw
        except (ValueError, UnicodeError):
            raise PresetError("CORRUPT_DATA", "A preset JSON file is invalid.") from None

    def _group_path(self, group_id: str) -> tuple[Path, Path, bool]:
        group_id = _identifier(group_id)
        found = []
        for root, readonly in ((self.bundled_root, True), (self.local_root, False)):
            path = root / group_id
            self._safe_path(root, path)
            if path.exists():
                if not path.is_dir():
                    raise PresetError("CORRUPT_DATA", "A preset group is not a directory.")
                found.append((root, path, readonly))
        if len(found) > 1:
            raise PresetError("CORRUPT_DATA", "Preset group identifiers must be globally unique.")
        if not found:
            raise PresetError("NOT_FOUND", "The preset group does not exist.")
        return found[0]

    @contextmanager
    def _group(self, group_id: str, *, write: bool = False):
        root, path, readonly = self._group_path(group_id)
        if write and readonly:
            raise PresetError("READ_ONLY", "Bundled presets are read-only; create a local group.")
        with self._guard(path / ".lock", writable=not readonly):
            if not readonly:
                self._recover(root, path)
            yield root, path, readonly

    def _recover(self, root: Path, group: Path):
        characters = group / "characters"
        self._safe_path(root, characters)
        if not characters.is_dir():
            raise PresetError("CORRUPT_DATA", "A preset group lacks its character directory.")
        for entry in sorted(characters.iterdir()):
            self._safe_path(root, entry)
            match = _BACKUP.fullmatch(entry.name)
            if match:
                target = characters / match.group(1)
                self._safe_path(root, target)
                if target.exists():
                    shutil.rmtree(entry)
                else:
                    os.replace(entry, target)
                _sync_directory(characters)
            elif _PENDING.fullmatch(entry.name):
                shutil.rmtree(entry)
                _sync_directory(characters)

    def _metadata(self, root: Path, group: Path) -> GroupMetadata:
        data, _ = self._read_json(root, group / "group.json")
        metadata = _model(GroupMetadata, data, disk=True)
        if metadata.id != group.name:
            raise PresetError("CORRUPT_DATA", "Preset group identifier does not match its folder.")
        return metadata

    def _character(self, root: Path, group: Path, character_id: str) -> dict:
        character_id = _identifier(character_id)
        path = group / "characters" / character_id
        self._safe_path(root, path)
        if not path.exists():
            raise PresetError("NOT_FOUND", "The character does not exist.")
        if not path.is_dir():
            raise PresetError("CORRUPT_DATA", "A character is not a directory.")
        data, raw_json = self._read_json(root, path / "character.json")
        character = _model(CharacterInformation, data, disk=True)
        if character.id != character_id:
            raise PresetError("CORRUPT_DATA", "Character identifier does not match its folder.")
        raw_markdown = self._read(root, path / "design.md", MAX_MARKDOWN_BYTES)
        try:
            markdown = raw_markdown.decode("utf-8")
        except UnicodeError:
            raise PresetError("CORRUPT_DATA", "Character Markdown must be UTF-8.") from None
        if not markdown.strip():
            raise PresetError("CORRUPT_DATA", "Character Markdown must not be empty.")
        return {
            "group_id": group.name,
            "character": character.model_dump(mode="json"),
            "design_markdown": markdown,
            "revision": _revision(raw_json, raw_markdown),
        }

    @staticmethod
    def _summary(character: dict) -> dict:
        return {
            "group_id": character["group_id"],
            **character["character"],
            "revision": character["revision"],
        }

    def _characters(self, root: Path, group: Path) -> list[dict]:
        directory = group / "characters"
        self._safe_path(root, directory)
        if not directory.is_dir():
            raise PresetError("CORRUPT_DATA", "A preset group lacks its character directory.")
        result = []
        for entry in sorted(directory.iterdir()):
            self._safe_path(root, entry)
            if entry.name.startswith("."):
                continue
            if _ID.fullmatch(entry.name) is None:
                raise PresetError("CORRUPT_DATA", "A character directory identifier is invalid.")
            result.append(self._summary(self._character(root, group, entry.name)))
        return result

    def _scenario(self, root: Path, group: Path) -> tuple[PresetScenario, str]:
        data, raw = self._read_json(root, group / "scenario.json")
        return _model(PresetScenario, data, disk=True), _revision(raw)

    def list_groups(self) -> list[dict]:
        ids = set()
        for root in (self.bundled_root, self.local_root):
            self._safe_path(root, root)
            if not root.exists():
                continue
            if not root.is_dir():
                raise PresetError("CORRUPT_DATA", "A preset library root is not a directory.")
            try:
                entries = sorted(root.iterdir())
            except OSError:
                raise PresetError("IO_ERROR", "Preset storage is unavailable.") from None
            for entry in entries:
                self._safe_path(root, entry)
                if entry.name.startswith("."):
                    continue
                if _ID.fullmatch(entry.name) is None or not entry.is_dir():
                    raise PresetError("CORRUPT_DATA", "A preset group directory is invalid.")
                if entry.name in ids:
                    raise PresetError("CORRUPT_DATA", "Preset group identifiers are duplicated.")
                ids.add(entry.name)
        result = []
        for group_id in sorted(ids):
            with self._group(group_id) as (root, path, readonly):
                metadata = self._metadata(root, path)
                # Validate all members, so malformed groups are never silently hidden.
                self._characters(root, path)
                self._scenario(root, path)
                result.append({**metadata.model_dump(mode="json"), "readonly": readonly})
        return result

    def create_group(self, group_id: str, title: str, description: str = "") -> dict:
        group_id = _identifier(group_id)
        metadata = _model(
            GroupMetadata, {"id": group_id, "title": title, "description": description}
        )
        self._safe_path(self.local_root, self.local_root)
        try:
            self.local_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.local_root, 0o700)
        except OSError:
            raise PresetError("IO_ERROR", "Preset storage is unavailable.") from None
        with self._guard(self.local_root / ".library.lock", writable=True):
            for root in (self.bundled_root, self.local_root):
                self._safe_path(root, root / group_id)
                if (root / group_id).exists():
                    raise PresetError(
                        "ALREADY_EXISTS", "The preset group identifier already exists."
                    )
            temporary = self.local_root / f".{group_id}.pending-{uuid.uuid4().hex}"
            scenario = PresetScenario(
                scenario_id=group_id,
                title=title,
                board={"rows": 8, "cols": 8},
            )
            try:
                temporary.mkdir(mode=0o700)
                (temporary / "characters").mkdir(mode=0o700)
                _write_private(
                    temporary / "group.json", _json_bytes(metadata.model_dump(mode="json"))
                )
                _write_private(
                    temporary / "scenario.json", _json_bytes(scenario.model_dump(mode="json"))
                )
                _sync_directory(temporary / "characters")
                _sync_directory(temporary)
                os.replace(temporary, self.local_root / group_id)
                _sync_directory(self.local_root)
            except OSError:
                raise PresetError("IO_ERROR", "The preset group could not be saved.") from None
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        return {**metadata.model_dump(mode="json"), "readonly": False}

    def get_group(self, group_id: str) -> dict:
        with self._group(group_id) as (root, path, readonly):
            metadata = self._metadata(root, path)
            scenario, revision = self._scenario(root, path)
            return {
                **metadata.model_dump(mode="json"),
                "readonly": readonly,
                "characters": self._characters(root, path),
                "scenario": scenario.model_dump(mode="json"),
                "scenario_revision": revision,
            }

    def list_characters(self) -> list[dict]:
        result = []
        for group in self.list_groups():
            result.extend(self.get_group(group["id"])["characters"])
        return result

    def get_character(self, group_id: str, character_id: str) -> dict:
        _identifier(character_id)
        with self._group(group_id) as (root, path, _):
            self._metadata(root, path)
            return self._character(root, path, character_id)

    def save_character(
        self,
        group_id: str,
        character_id: str,
        character: dict,
        design_markdown: str,
        expected_revision: str | None = None,
    ) -> dict:
        character_id = _identifier(character_id)
        information = _model(CharacterInformation, character)
        if information.id != character_id:
            raise PresetError("INVALID_DATA", "Character identifier must match its folder.")
        try:
            markdown = design_markdown.encode("utf-8")
        except (AttributeError, UnicodeError):
            raise PresetError("INVALID_DATA", "Character Markdown must be UTF-8 text.") from None
        if not design_markdown.strip() or len(markdown) > MAX_MARKDOWN_BYTES:
            raise PresetError(
                "INVALID_DATA", "Character Markdown must contain 1–262144 UTF-8 bytes."
            )
        encoded = _json_bytes(information.model_dump(mode="json"))
        with self._group(group_id, write=True) as (root, group, _):
            self._metadata(root, group)
            directory = group / "characters"
            target = directory / character_id
            self._safe_path(root, target)
            current = self._character(root, group, character_id) if target.exists() else None
            if current is not None and expected_revision is None:
                raise PresetError(
                    "ALREADY_EXISTS", "The character already exists; load it before editing."
                )
            if expected_revision is not None and (
                current is None or current["revision"] != expected_revision
            ):
                raise PresetError(
                    "REVISION_CONFLICT", "The character changed; reload before saving."
                )
            pending = directory / f".{character_id}.pending-{uuid.uuid4().hex}"
            backup = directory / f".{character_id}.backup"
            try:
                pending.mkdir(mode=0o700)
                _write_private(pending / "character.json", encoded)
                _write_private(pending / "design.md", markdown)
                _sync_directory(pending)
                if target.exists():
                    os.replace(target, backup)
                    _sync_directory(directory)
                os.replace(pending, target)
                _sync_directory(directory)
                if backup.exists():
                    shutil.rmtree(backup)
                    _sync_directory(directory)
            except OSError:
                raise PresetError("IO_ERROR", "The character could not be saved.") from None
            finally:
                # Also runs on ordinary exceptions; process crashes recover on the next access.
                self._recover(root, group)
            return self._character(root, group, character_id)

    def save_scenario(
        self, group_id: str, scenario: dict, expected_revision: str | None = None
    ) -> dict:
        _identifier(group_id)
        _, _, readonly = self._group_path(group_id)
        if readonly:
            raise PresetError("READ_ONLY", "Bundled presets are read-only; create a local group.")
        config = _model(PresetScenario, scenario)
        # Resolve before acquiring the writer lock, avoiding cross-group lock ordering.
        self._resolve(config)
        encoded = _json_bytes(config.model_dump(mode="json"))
        with self._group(group_id, write=True) as (root, group, _):
            self._metadata(root, group)
            _, current_revision = self._scenario(root, group)
            if expected_revision is not None and expected_revision != current_revision:
                raise PresetError(
                    "REVISION_CONFLICT", "The scenario changed; reload before saving."
                )
            target = group / "scenario.json"
            temporary = group / f".scenario.pending-{uuid.uuid4().hex}"
            try:
                _write_private(temporary, encoded)
                os.replace(temporary, target)
                _sync_directory(group)
            except OSError:
                raise PresetError("IO_ERROR", "The scenario could not be saved.") from None
            finally:
                temporary.unlink(missing_ok=True)
            return {"scenario": config.model_dump(mode="json"), "revision": _revision(encoded)}

    def _resolve(self, config: PresetScenario) -> tuple[Scenario, dict[str, str]]:
        pieces = []
        personas = {}
        for reference in config.characters:
            source = self.get_character(reference.group_id, reference.character_id)
            information = dict(source["character"])
            piece_id = reference.piece_id or reference.character_id
            if piece_id in personas:
                raise PresetError("INVALID_DATA", "Resolved piece identifiers must be unique.")
            information["id"] = piece_id
            if reference.position is not None:
                information["position"] = reference.position.model_dump(mode="json")
            pieces.append(_model(PieceConfig, information))
            personas[piece_id] = source["design_markdown"]
        scenario_data = config.model_dump(mode="json", exclude={"characters"})
        scenario_data["pieces"] = [piece.model_dump(mode="json") for piece in pieces]
        return _model(Scenario, scenario_data), personas

    def build_scenario(self, group_id: str) -> tuple[Scenario, dict[str, str]]:
        group = self.get_group(group_id)
        return self._resolve(_model(PresetScenario, group["scenario"], disk=True))
