"""Server-only configuration. Secrets are never serialized into application state."""

from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseModel):
    api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    base_url: str = ""
    model: str = ""
    provider_profile: str = "generic"
    response_format: str = "json_schema"
    tokenizer_name: str = "cl100k_base"
    tokenizer_agreement: bool = False
    timeout_seconds: float = 45
    context_capacity: int = Field(default=16384, ge=4096)
    data_dir: Path = ROOT / "data"

    @property
    def configured(self) -> bool:
        return bool(self.api_key.get_secret_value() and self.base_url and self.model)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        import os

        values = dict(dotenv_values(path or ROOT / ".env.local"))
        values.update({k: v for k, v in os.environ.items() if k.startswith("CHESSX_")})

        def get(name: str, default: str = "") -> str:
            return values.get("CHESSX_" + name) or default

        data = Path(get("DATA_DIR", "data"))
        if not data.is_absolute():
            data = ROOT / data
        return cls(
            api_key=SecretStr(get("API_KEY")),
            base_url=get("BASE_URL"),
            model=get("MODEL"),
            provider_profile=get("PROVIDER_PROFILE", "generic"),
            response_format=get("RESPONSE_FORMAT", "json_schema"),
            tokenizer_name=get("TOKENIZER_NAME", "cl100k_base"),
            tokenizer_agreement=get("TOKENIZER_AGREEMENT", "false").lower() == "true",
            timeout_seconds=float(get("TIMEOUT_SECONDS", "45")),
            context_capacity=int(get("CONTEXT_CAPACITY", "16384")),
            data_dir=data,
        )
