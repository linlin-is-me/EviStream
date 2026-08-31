"""Generate the fully synthetic Stage 0 video fixture with FFmpeg."""

import argparse
import hashlib
import os
import subprocess
from pathlib import Path


def _filter_path(path: Path) -> str:
    """Escape a local path for use inside an FFmpeg filter expression."""
    return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def _default_font() -> Path:
    candidates = (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("No supported font found; pass --font-file explicitly")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/media/stage0_sample.mp4"),
    )
    parser.add_argument(
        "--ffmpeg",
        default=os.getenv("EVISTREAM_FFMPEG_BINARY", "ffmpeg"),
    )
    parser.add_argument("--font-file", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    font_file = args.font_file or _default_font()

    speech = (
        "EviStream stage zero media verification. "
        "Evidence must have a source and a time range. "
        "This synthetic recording is safe to distribute."
    )
    command = [
        args.ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=24:duration=30",
        "-f",
        "lavfi",
        "-i",
        f"flite=text='{speech}':voice=slt",
        "-filter_complex",
        f"[0:v]drawtext=fontfile='{_filter_path(font_file)}':"
        "text='EviStream Stage 0':x=(w-text_w)/2:y=24:"
        "fontsize=30:fontcolor=white:box=1:boxcolor=black@0.55[v];[1:a]apad=pad_dur=30[a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        "30",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "30",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        str(output),
    ]
    subprocess.run(command, check=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    print(output)
    print(checksum_path)


if __name__ == "__main__":
    main()
