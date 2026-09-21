# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Viewport(Strict):
    x: float = Field(default=60, ge=-1e7, le=1e7)
    y: float = Field(default=60, ge=-1e7, le=1e7)
    k: float = Field(default=1, ge=0.1, le=3)


class Parameters(Strict):
    width: int = Field(default=1344, ge=256, le=4096)
    height: int = Field(default=768, ge=256, le=4096)
    num_frames: int = Field(default=107, ge=1, le=363)
    seed: int = Field(default=42, ge=0, le=2147483647)
    lora_scale: float = Field(default=1, ge=0, le=2)
    # Native H3 counts sigma positions. UI variants select actual denoiser
    # evaluations and resolve this field internally, never exposing a raw knob.
    num_inference_steps: int | None = Field(default=None, ge=2, le=101)


class Node(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    kind: Literal["text", "image", "video", "audio", "generate"]
    x: float = Field(ge=-100000, le=100000)
    y: float = Field(ge=-100000, le=100000)
    title: str = Field(default="", max_length=160)
    text: str = Field(default="", max_length=20000)
    asset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    workflow: str = Field(default="h3-t2va", max_length=80)
    service_id: str = Field(default="", max_length=80)
    parameters: Parameters = Field(default_factory=Parameters)
    origin_run: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class Edge(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    source: str = Field(max_length=80)
    target: str = Field(max_length=80)


class Document(Strict):
    title: str = Field(default="未命名画布", min_length=1, max_length=120)
    revision: int = Field(default=0, ge=0)
    nodes: list[Node] = Field(default_factory=list, max_length=300)
    edges: list[Edge] = Field(default_factory=list, max_length=600)
    viewport: Viewport = Field(default_factory=Viewport)
    placed_runs: list[str] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def graph(self):
        nodes = {n.id: n for n in self.nodes}
        if len(nodes) != len(self.nodes) or len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("Duplicate node or edge identity")
        pairs = set()
        graph = {key: [] for key in nodes}
        for edge in self.edges:
            if edge.source not in nodes or edge.target not in nodes or edge.source == edge.target:
                raise ValueError("Connection references a missing node or itself")
            pair = (edge.source, edge.target)
            if pair in pairs:
                raise ValueError("Duplicate connection")
            pairs.add(pair)
            graph[edge.source].append(edge.target)
        visiting, done = set(), set()

        def visit(key):
            if key in visiting:
                raise ValueError("Connections must not contain a cycle")
            if key in done:
                return
            visiting.add(key)
            for target in graph[key]:
                visit(target)
            visiting.remove(key)
            done.add(key)

        for key in nodes:
            visit(key)
        return self


class Service(Strict):
    id: str = Field(default="", pattern=r"^[a-f0-9]{0,32}$")
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["h3-local", "h3-api", "image-api", "image-local"] = "h3-local"
    model: str = Field(default="", max_length=1000)
    base_url: str = Field(default="", max_length=2000)
    api_key: str | None = Field(default=None, max_length=4096)
    partition: Literal["fl2va", "ref2va"] = "fl2va"
    image_edit: bool = False
    checkpoint: Literal["", "z-image-turbo", "z-image"] = ""
    runtime_id: str = Field(default="", max_length=80)
    gpu_uuids: list[str] = Field(default_factory=list, max_length=32)
    transformer_path: str = Field(default="", max_length=2000)
    lora_path: str = Field(default="", max_length=2000)
    execution_mode: Literal["fast", "standard"] = "fast"
    attention_backend: Literal["FLASH_ATTN_V100", "FLASHINFER_SM70", "TORCH_SDPA", "FASTVIDEO_VSA"] = (
        "FLASH_ATTN_V100"
    )


WORKFLOWS = [
    {
        "id": "image-text",
        "name": "文字生图",
        "name_en": "Text to image",
        "output": "image",
        "provider": "image-api",
        "min_images": 0,
        "max_images": 0,
    },
    {
        "id": "image-edit",
        "name": "参考图编辑",
        "name_en": "Image editing",
        "output": "image",
        "provider": "image-api",
        "min_images": 1,
        "max_images": 1,
    },
    {
        "id": "h3-t2va",
        "name": "文字生成视频",
        "name_en": "Text to video",
        "output": "video",
        "provider": "h3",
        "partition": "fl2va",
        "task": "t2va",
        "min_images": 0,
        "max_images": 0,
    },
    {
        "id": "h3-first",
        "name": "首帧生成视频",
        "name_en": "First frame to video",
        "output": "video",
        "provider": "h3",
        "partition": "fl2va",
        "task": "fl2va",
        "min_images": 1,
        "max_images": 1,
        "keyframe_indices": [0],
    },
    {
        "id": "h3-last",
        "name": "尾帧生成视频",
        "name_en": "Last frame to video",
        "output": "video",
        "provider": "h3",
        "partition": "fl2va",
        "task": "fl2va",
        "min_images": 1,
        "max_images": 1,
        "keyframe_indices": [-1],
    },
    {
        "id": "h3-frames",
        "name": "首尾帧生成视频",
        "name_en": "First and last frames",
        "output": "video",
        "provider": "h3",
        "partition": "fl2va",
        "task": "fl2va",
        "min_images": 2,
        "max_images": 2,
        "keyframe_indices": [0, -1],
    },
    {
        "id": "h3-reference",
        "name": "参考素材生成视频",
        "name_en": "Reference to video",
        "output": "video",
        "provider": "h3",
        "partition": "ref2va",
        "task": "ref2va",
        "min_images": 0,
        "max_images": 9,
        "experimental": True,
    },
]


def compatible(service: dict, workflow: dict) -> bool:
    from .fasth3 import is_service

    if is_service(service) and workflow["id"] != "h3-t2va":
        return False
    if workflow["provider"] == "image-api":
        return service["kind"] in {"image-api", "image-local"} and (
            workflow["id"] != "image-edit" or service.get("image_edit", False)
        )
    return service["kind"].startswith("h3-") and service["partition"] == workflow["partition"]
