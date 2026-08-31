from io import BytesIO

import pytest

from evistream.storage.artifacts import LocalArtifactStore


def test_local_artifact_store_round_trip(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    store = LocalArtifactStore(tmp_path / "artifacts")

    copied_uri = store.put_file(source, "videos/a/source.txt")
    streamed_uri = store.put_stream(BytesIO(b"stream"), "videos/a/stream.bin")
    text_uri = store.write_text("text", "videos/a/result.json")

    assert copied_uri == "artifact://videos/a/source.txt"
    assert store.resolve(copied_uri).read_text(encoding="utf-8") == "source"
    assert store.resolve(streamed_uri).read_bytes() == b"stream"
    assert store.resolve(text_uri).read_text(encoding="utf-8") == "text"


@pytest.mark.parametrize("key", ["../escape", "/absolute", "a/../../escape", ""])
def test_local_artifact_store_rejects_unsafe_keys(tmp_path, key: str) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError, match="artifact key"):
        store.path_for_key(key)


def test_local_artifact_store_rejects_unknown_uri(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError, match="unsupported artifact URI"):
        store.resolve("file:///tmp/a")
