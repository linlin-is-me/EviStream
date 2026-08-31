# Stage 0 fixture data

`tests/fixtures/media/stage0_sample.mp4` is generated entirely by
`scripts/generate_stage0_fixture.py`. FFmpeg creates the test pattern, overlay text, and synthetic
Flite speech. No external video, voice recording, personal information, or restricted dataset is
included.

The fixture is 30 seconds at 640×360 with H.264 video and AAC audio. Its adjacent `.sha256` file
records the committed artifact hash. Regeneration may produce a different byte hash across FFmpeg
versions, so changes must include a new checksum and a successful FFprobe/ASR verification.

