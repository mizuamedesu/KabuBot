from __future__ import annotations

from dataclasses import dataclass, field


SOFTWARE_TICKERS = [
    "MSFT", "ORCL", "CRM", "ADBE", "NOW", "INTU", "SNOW", "DDOG", "NET",
    "CRWD", "ZS", "MDB", "TEAM", "WDAY", "HUBS", "BILL", "DOCU", "PATH",
    "ESTC", "OKTA", "S", "PLTR", "U", "APP", "GTLB", "CFLT", "AI", "SOUN",
    "4478.T", "4443.T", "3994.T", "5032.T", "5253.T"
]

SEMICONDUCTOR_TICKERS = [
    "NVDA", "AMD", "AVGO", "TSM", "ASML", "MU", "ARM", "MRVL", "SMCI", "SOXX"
]

AI_INFRA_TICKERS = [
    "NVDA", "AMD", "AVGO", "TSM", "SMCI", "DELL", "VRT", "ANET", "CEG", "ETN"
]


@dataclass(frozen=True)
class SectorSpec:
    query: str
    yahoo_sector: str | None
    keywords: list[str]
    default_tickers: list[str] = field(default_factory=list)


def sector_spec(query: str) -> SectorSpec:
    text = query.lower()
    if any(term in text for term in ["software", "saas", "ソフト", "ソフトウェア", "クラウド"]):
        return SectorSpec(
            query=query,
            yahoo_sector="Technology",
            keywords=["software", "application", "saas", "cloud", "クラウド", "ソフトウェア"],
            default_tickers=SOFTWARE_TICKERS,
        )
    if any(term in text for term in ["semiconductor", "chip", "半導体"]):
        return SectorSpec(
            query=query,
            yahoo_sector="Technology",
            keywords=["semiconductor", "chip", "半導体"],
            default_tickers=SEMICONDUCTOR_TICKERS,
        )
    if any(term in text for term in ["ai", "人工知能", "llm", "gpu", "データセンター"]):
        return SectorSpec(
            query=query,
            yahoo_sector="Technology",
            keywords=["ai", "artificial intelligence", "llm", "gpu", "data center"],
            default_tickers=AI_INFRA_TICKERS + SOFTWARE_TICKERS[:14],
        )
    if any(term in text for term in ["health", "biotech", "ヘルス", "バイオ"]):
        return SectorSpec(
            query=query,
            yahoo_sector="Healthcare",
            keywords=["healthcare", "biotech", "pharma", "medical"],
        )
    if any(term in text for term in ["energy", "oil", "gas", "電力", "エネルギー"]):
        return SectorSpec(
            query=query,
            yahoo_sector="Energy",
            keywords=["energy", "oil", "gas", "power", "utility"],
        )
    return SectorSpec(
        query=query,
        yahoo_sector=None,
        keywords=[term for term in text.replace(",", " ").split() if term],
        default_tickers=SOFTWARE_TICKERS if not text.strip() else [],
    )

