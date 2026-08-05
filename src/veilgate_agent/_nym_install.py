from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import quote
from urllib.request import urlopen


REPOSITORY = "Wismut/nym-pii-multilingual-small"
REVISION = "4348999cd3c2e20c49615e9af7c6bbb45b64cd85"
FILES = (
    ("edge-int8/model_int8.onnx", "model.onnx", 108_233_527, "e9a3a8c8cd55b3bcf329de5a9307cfae5053ce93c00330c33facd022e60daa17"),
    ("edge-int8/config.json", "config.json", 5_688, "3f07065571e22bb73eba28ddb1fae4509c0cf703762e3bfa7a6ab2111cf7cb88"),
    ("edge-int8/tokenizer.json", "tokenizer.json", 12_389_891, "c299144e68dfec1dc536204a7ae3712710c5c6cade9269a83f5042250d47d8de"),
    ("tokenizer_config.json", "tokenizer_config.json", 573, "02055b886266b1a475bc324da83e273fe96e4974002f9d970f26e94aa73e5885"),
)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(path: Path, size: int, sha256: str) -> None:
    if path.stat().st_size != size or _digest(path) != sha256:
        raise RuntimeError(f"Nym 模型文件校验失败：{path.name}")


def install(target: Path) -> Path:
    if target.is_dir():
        try:
            for _source, name, size, digest in FILES:
                _verify(target / name, size, digest)
            return target
        except (OSError, RuntimeError):
            pass
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".nym-edge-", dir=str(target.parent)))
    try:
        for source, name, size, digest in FILES:
            encoded_source = "/".join(quote(part) for part in source.split("/"))
            url = (
                f"https://huggingface.co/{REPOSITORY}/resolve/"
                f"{REVISION}/{encoded_source}"
            )
            output = temporary / name
            with urlopen(url, timeout=60) as response, output.open("xb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
            _verify(output, size, digest)
        if target.exists():
            shutil.rmtree(target)
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="安装并校验 Nym Edge INT8 模型")
    parser.add_argument(
        "--target",
        type=Path,
        default=Path.home() / ".anxinai" / "models" / "nym-edge-int8",
    )
    args = parser.parse_args()
    try:
        target = install(args.target.expanduser().resolve())
        print(json.dumps(
            {"status": "installed", "model_directory": str(target)},
            ensure_ascii=False,
        ))
    except Exception as error:
        print(json.dumps(
            {"status": "error", "message": str(error)},
            ensure_ascii=False,
        ))
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
