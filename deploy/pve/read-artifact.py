"""Extract only the four files expected from a private CI release stream."""
from pathlib import Path
import shutil
import sys
import tarfile

MAX_BYTES = {
    "compose.yaml": 64 * 1024,
    "SHA256SUMS": 1024,
    "IMAGE_ID": 128,
    "trendradar-image.tar.gz": 2 * 1024**3,
}


class ReleaseMember(tarfile.TarInfo):
    def _proc_member(self, archive):
        # Reject extension headers before tarfile expands them.
        if (self.name not in MAX_BYTES or self.type != tarfile.REGTYPE
                or not 0 < self.size <= MAX_BYTES[self.name]):
            raise ValueError("Unexpected release artifact member")
        return super()._proc_member(archive)


def extract(source, destination):
    received = set()
    with tarfile.open(fileobj=source, mode="r|", tarinfo=ReleaseMember) as archive:
        for member in archive:
            if member.name in received:
                raise ValueError("Duplicate release artifact member")
            with archive.extractfile(member) as encoded, (destination / member.name).open("xb") as target:
                shutil.copyfileobj(encoded, target)
            received.add(member.name)
    if received != MAX_BYTES.keys():
        raise ValueError("Missing release artifact members")
    # Finish reading the SSH stream, including any final tar padding.
    while source.read(64 * 1024):
        pass


if __name__ == "__main__":
    try:
        extract(sys.stdin.buffer, Path(sys.argv[1]))
    except (tarfile.TarError, OSError, ValueError) as error:
        sys.exit(f"Release artifact rejected: {type(error).__name__}")
