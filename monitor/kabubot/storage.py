from __future__ import annotations

from pathlib import Path

from .codex_client import render_report_markdown
from .types import ScanReport


class ReportStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.reports_dir = data_dir / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def save(self, report: ScanReport) -> None:
        json_path = self.reports_dir / f"{report.id}.json"
        markdown_path = self.reports_dir / f"{report.id}.md"
        json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        markdown_path.write_text(render_report_markdown(report), encoding="utf-8")
        (self.data_dir / "latest.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (self.data_dir / "latest.md").write_text(render_report_markdown(report), encoding="utf-8")

    def latest_markdown(self) -> str | None:
        path = self.data_dir / "latest.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def latest_json(self) -> str | None:
        path = self.data_dir / "latest.json"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

