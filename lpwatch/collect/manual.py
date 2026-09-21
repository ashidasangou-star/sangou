"""手動投入の経路。

data/lpwatch/inbox/ に置かれた .txt を読む。告知の本文をそのまま
貼り付けたファイルでよい。自動収集が使えない日でもツールが
回り続けるようにするための、pokebox_ev と同じ逃げ道。

読み終えたファイルは done/ に移し、二度読まない。
"""

from __future__ import annotations

from pathlib import Path

from .. import extract
from ..model import Reception, now_jst


class ManualCollector:
    name = "manual"

    def __init__(self, inbox: Path | str) -> None:
        self.inbox = Path(inbox)

    def pending(self) -> list[Path]:
        if not self.inbox.exists():
            return []
        return sorted(p for p in self.inbox.glob("*.txt") if p.is_file())

    def collect(self, *, archive: bool = True) -> list[Reception]:
        out: list[Reception] = []
        done = self.inbox / "done"
        for path in self.pending():
            text = path.read_text(encoding="utf-8")
            out.extend(extract.extract(text, source=path.name, ref=now_jst()))
            if archive:
                done.mkdir(parents=True, exist_ok=True)
                path.rename(done / path.name)
        return out
