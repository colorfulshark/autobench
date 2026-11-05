"""Path checks shared by raw artifact downloads and generated reports."""

from pathlib import Path, PurePosixPath


class UnsafeArtifactPath(ValueError):
    pass


def result_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise UnsafeArtifactPath("无效的产物路径")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise UnsafeArtifactPath("不允许访问符号链接产物")
    if not path.resolve().is_relative_to(root.resolve()):
        raise UnsafeArtifactPath("产物路径越界")
    return path
