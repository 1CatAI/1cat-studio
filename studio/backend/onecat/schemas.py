# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StudioSettings(StrictModel):
    model_directory: str = Field(min_length=1)
    modelscope_endpoint: str
    idle_unload_minutes: int = Field(ge=0, le=1440, strict=True)
    autostart_profile: str | None
    host: Literal["127.0.0.1", "0.0.0.0"]
    port: int = Field(ge=1024, le=65535, strict=True)
    locale: Literal["zh-CN", "en"]
    theme: Literal["light", "dark", "system"]
    update_channel: str | None = None

    @field_validator("model_directory")
    @classmethod
    def valid_directory(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("Enter a model directory")
        return value

    @field_validator("modelscope_endpoint")
    @classmethod
    def valid_endpoint(cls, value):
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(c.isspace() for c in value)
        ):
            raise ValueError("Enter a valid HTTPS ModelScope endpoint without credentials")
        # Accessing port also rejects invalid/non-numeric ports.
        _ = parsed.port
        return value


class RuntimeImport(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    python_path: str
    working_directory: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)

    @field_validator("environment")
    @classmethod
    def valid_env(cls, values):
        for key, value in values.items():
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or "\x00" in value:
                raise ValueError("Invalid environment variable")
            if key in {"HOME", "CODEX_HOME", "PYTHONSTARTUP", "LD_PRELOAD"}:
                raise ValueError(f"Managed runtime cannot override {key}")
        return values


class Profile(StrictModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    model_path: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    served_model_name: str = Field(default="onecat-model", min_length=1, max_length=100)
    gpu_uuids: list[str] = Field(default_factory=list)
    tensor_parallel_size: int = Field(default=1, ge=1, le=64)
    dtype: Literal["half", "bfloat16", "auto"] = "half"
    quantization: str | None = None
    kv_cache_dtype: str = "auto"
    max_model_len: int = Field(default=32768, ge=512, le=2097152)
    max_num_batched_tokens: int = Field(default=4096, ge=256, le=2097152)
    max_num_seqs: int = Field(default=1, ge=1, le=4096)
    gpu_memory_utilization: float = Field(default=0.8, ge=0.1, le=0.98)
    attention_backend: str | None = None
    enforce_eager: bool = False
    enable_prefix_caching: bool = True
    speculative_config: dict | None = None
    extra_args: list[str] = Field(default_factory=list)
    default_sampling: dict = Field(
        default_factory=lambda: {
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": None,
            "thinking": False,
        }
    )
    hardware_profile: dict | None = None
    source: str = "custom"
    catalog_id: str | None = None
    tool_calling: bool = False
    tool_parser: str | None = None
    vision_enabled: bool = False
    max_images: int = Field(default=4, ge=1, le=4)
    vision_processor_kwargs: dict = Field(default_factory=dict)

    @field_validator("gpu_uuids")
    @classmethod
    def valid_gpus(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("GPU UUIDs must be unique")
        if any(not re.fullmatch(r"GPU-[a-fA-F0-9-]{36}", v) for v in values):
            raise ValueError("Use GPU UUIDs from the device list")
        return values

    @field_validator("served_model_name")
    @classmethod
    def valid_alias(cls, value):
        if not re.fullmatch(r"[a-zA-Z0-9_./:-]+", value):
            raise ValueError("Model alias must use letters, numbers, slash, colon, dot or dash")
        return value

    @field_validator("extra_args")
    @classmethod
    def validate_args(cls, values):
        # One structured owner for process binding and hardware identity.
        owned = {
            "--host",
            "--port",
            "--api-key",
            "--model",
            "--served-model-name",
            "--tensor-parallel-size",
            "-tp",
            "--dtype",
            "--quantization",
            "--kv-cache-dtype",
            "--max-model-len",
            "--max-num-batched-tokens",
            "--max-num-seqs",
            "--gpu-memory-utilization",
            "--speculative-config",
            "--attention-backend",
            "--enforce-eager",
            "--enable-prefix-caching",
            "--no-enable-prefix-caching",
            "--enable-auto-tool-choice",
            "--tool-call-parser",
            "--limit-mm-per-prompt",
            "--mm-processor-kwargs",
            "--ssl-keyfile",
            "--ssl-certfile",
        }
        if any("\x00" in v or v.split("=", 1)[0] in owned for v in values):
            raise ValueError("Extra arguments cannot override managed fields")
        return values

    @model_validator(mode="after")
    def check_topology(self):
        if self.gpu_uuids and len(self.gpu_uuids) != self.tensor_parallel_size:
            raise ValueError("Tensor parallel size must equal the number of selected GPUs")
        if self.hardware_profile is not None:
            self.hardware_profile = HardwareSetting.model_validate(
                self.hardware_profile
            ).model_dump()
        allowed_sampling = {"temperature", "top_p", "max_tokens", "thinking", "seed"}
        if self.default_sampling.keys() - allowed_sampling:
            raise ValueError("Unsupported default sampling field")
        if (
            not 0 <= float(self.default_sampling.get("temperature", 0.7)) <= 2
            or not 0 < float(self.default_sampling.get("top_p", 0.9)) <= 1
        ):
            raise ValueError("Invalid default temperature or top_p")
        limit = self.default_sampling.get("max_tokens")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise ValueError("Output token limit must be automatic (null) or a positive integer")
        if self.tool_calling and not self.tool_parser:
            raise ValueError("Choose a verified tool parser")
        return self


class HardwareSetting(StrictModel):
    power_limit_w: float | None = Field(default=None, ge=1, le=2000)
    graphics_clock_mhz: int | None = Field(default=None, ge=100, le=5000)
    reset_clocks: bool = False

    @model_validator(mode="after")
    def check_clock(self):
        if self.reset_clocks and self.graphics_clock_mhz is not None:
            raise ValueError("Choose a clock lock or a reset, not both")
        return self


class BenchmarkRequest(StrictModel):
    profile_id: str
    prompt_tokens: int = Field(default=8192, ge=128, le=131072)
    output_tokens: int = Field(default=1024, ge=16, le=8192)
    repeats: int = Field(default=3, ge=2, le=10)
    candidates: list[HardwareSetting] | None = Field(default=None, min_length=2, max_length=12)
    min_prefill_tokens_s: float = Field(default=0, ge=0)
    min_decode_tokens_s: float = Field(default=0, ge=0)
    max_ttft_s: float | None = Field(default=None, gt=0)


class SetupRequest(StrictModel):
    password: str = Field(min_length=8, max_length=256)
