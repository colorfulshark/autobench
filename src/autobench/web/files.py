from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

import yaml


class ConflictError(ValueError):
    pass


def revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_document(text: str) -> dict:
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ValueError("YAML 顶层必须是映射对象")

    def check(value, ancestors: set[int]):
        if isinstance(value, (dict, list)):
            if id(value) in ancestors:
                raise ValueError("配置不支持循环 YAML 引用")
            ancestors = ancestors | {id(value)}
            if isinstance(value, dict):
                if any(not isinstance(key, str) for key in value):
                    raise ValueError("配置字段名必须是字符串")
                values = value.values()
            else:
                values = value
            for item in values:
                check(item, ancestors)
        elif value is not None and type(value) not in (str, int, float, bool):
            raise ValueError("配置仅支持字符串、数字、布尔值、列表、映射和 null")

    check(document, set())
    json.dumps(document, allow_nan=False)
    return document


class ConfigFiles:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir.resolve()
        self.lock = threading.Lock()

    def path(self, value: str | Path) -> Path:
        if not str(value).strip():
            raise ValueError("请填写配置文件路径")
        path = Path(value).expanduser()
        return (self.base_dir / path).resolve()

    def load(self, value: str | Path, expected_revision: str | None = None) -> dict:
        path = self.path(value)
        # Decode bytes directly: newline conversion would hide external CRLF changes.
        text = path.read_bytes().decode("utf-8")
        current = revision(text)
        if expected_revision is not None and expected_revision != current:
            raise ConflictError("配置已被外部修改，请重新加载或另存为")
        parse_error = None
        try:
            document = parse_document(text)
        except (yaml.YAMLError, ValueError) as exc:
            document = None
            mark = getattr(exc, "problem_mark", None)
            parse_error = (
                f"YAML 格式错误：第 {mark.line + 1} 行，第 {mark.column + 1} 列"
                if mark
                else "配置结构错误，请在 YAML 源码中修正"
            )
        return {
            "path": str(path),
            "text": text,
            "revision": current,
            "document": document,
            "parse_error": parse_error,
        }

    def save(self, value: str, text: str, expected_revision: str | None) -> dict:
        document = parse_document(text)
        path = self.path(value)
        # Serialize saves by all browser tabs served by this process.
        with self.lock:
            current = revision(path.read_bytes().decode("utf-8")) if path.exists() else None
            if current != expected_revision:
                raise ConflictError("目标文件已存在或已被外部修改，请重新加载或另存为")
            fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(text.encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                # Recheck immediately before committing the captured document.
                latest = revision(path.read_bytes().decode("utf-8")) if path.exists() else None
                if latest != current:
                    raise ConflictError("配置已被外部修改，请重新加载或另存为")
                os.replace(temporary, path)
                directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return {"path": str(path), "text": text, "revision": revision(text), "document": document}
