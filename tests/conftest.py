"""Shared fixtures: fake PyMuPDF pages built from compact span descriptions."""

from __future__ import annotations

from dataclasses import dataclass, field


def span(text: str, font: str, size: float, *, superscript: bool = False) -> dict:
    return {"text": text, "font": font, "size": size, "flags": 1 if superscript else 0}


def line(*spans: dict, x0: float = 55.0, y0: float = 100.0) -> dict:
    return {"bbox": (x0, y0, x0 + 300.0, y0 + 12.0), "spans": list(spans)}


@dataclass
class FakePage:
    """Stands in for a PyMuPDF page in tests."""

    lines: list[dict] = field(default_factory=list)
    image_blocks: int = 0

    def get_text(self, option: str) -> dict:
        assert option == "dict"
        blocks: list[dict] = [{"type": 1} for _ in range(self.image_blocks)]
        blocks.extend({"type": 0, "lines": [entry]} for entry in self.lines)
        return {"blocks": blocks}


def body(text: str, *, x0: float = 55.0, y0: float = 100.0) -> dict:
    return line(span(text, "Practice-Regular", 10.4), x0=x0, y0=y0)


def note_start(number: int, text: str, *, x0: float = 48.0, y0: float = 100.0) -> dict:
    return line(
        span(f"{number}\t {text}", "Practice-Bold", 5.5),
        x0=x0,
        y0=y0,
    )


def note_cont(text: str, *, x0: float = 57.0, y0: float = 110.0) -> dict:
    return line(span(text, "EuclidCircularB-Regular", 6.5), x0=x0, y0=y0)
