from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StockMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    company_name: str = ""
    sector: str = "Unknown"
    industry: str = ""
    country: str = "US"
    exchange: str = "NYSE"
    currency: str = "USD"
    market_cap_category: str = ""
    avg_volume: int = 0
    is_liquid: bool = True

    @property
    def key(self) -> str:
        return self.ticker.upper()


class StockUniverse(BaseModel):
    stocks: list[StockMetadata] = Field(default_factory=list)
    name: str = "default"
    description: str = ""

    def by_sector(self) -> dict[str, list[StockMetadata]]:
        result: dict[str, list[StockMetadata]] = {}
        for s in self.stocks:
            result.setdefault(s.sector, []).append(s)
        return result

    def by_industry(self) -> dict[str, list[StockMetadata]]:
        result: dict[str, list[StockMetadata]] = {}
        for s in self.stocks:
            key = s.industry or s.sector
            result.setdefault(key, []).append(s)
        return result

    def by_country(self) -> dict[str, list[StockMetadata]]:
        result: dict[str, list[StockMetadata]] = {}
        for s in self.stocks:
            result.setdefault(s.country, []).append(s)
        return result

    def by_exchange(self) -> dict[str, list[StockMetadata]]:
        result: dict[str, list[StockMetadata]] = {}
        for s in self.stocks:
            result.setdefault(s.exchange, []).append(s)
        return result

    def filter(
        self,
        *,
        sector: str | None = None,
        industry: str | None = None,
        country: str | None = None,
        exchange: str | None = None,
        currency: str | None = None,
        min_liquid: bool | None = None,
        min_liquidity: bool | None = None,
        tickers: set[str] | None = None,
    ) -> StockUniverse:
        def matches(value: str, expected: str | None) -> bool:
            return expected is None or value.casefold() == expected.casefold()

        filtered = list(self.stocks)
        filtered = [s for s in filtered if matches(s.sector, sector)]
        filtered = [s for s in filtered if matches(s.industry, industry)]
        filtered = [s for s in filtered if matches(s.country, country)]
        filtered = [s for s in filtered if matches(s.exchange, exchange)]
        filtered = [s for s in filtered if matches(s.currency, currency)]
        liquidity_filter = min_liquid if min_liquid is not None else min_liquidity
        if liquidity_filter is not None:
            filtered = [s for s in filtered if s.is_liquid == liquidity_filter]
        if tickers is not None:
            normalized = {t.upper() for t in tickers}
            filtered = [s for s in filtered if s.ticker.upper() in normalized]
        return StockUniverse(stocks=filtered, name=self.name, description=self.description)

    def tickers(self) -> list[str]:
        return [s.ticker for s in self.stocks]

    def get(self, ticker: str) -> StockMetadata | None:
        upper = ticker.upper()
        for s in self.stocks:
            if s.ticker.upper() == upper:
                return s
        return None

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> StockUniverse:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def sector_counts(self) -> list[dict[str, Any]]:
        groups = self.by_sector()
        return [
            {"name": sector, "count": len(stocks)}
            for sector, stocks in sorted(groups.items(), key=lambda x: -len(x[1]))
        ]

    def country_counts(self) -> list[dict[str, Any]]:
        groups = self.by_country()
        return [
            {"name": country, "count": len(stocks)}
            for country, stocks in sorted(groups.items(), key=lambda x: -len(x[1]))
        ]

    def exchange_counts(self) -> list[dict[str, Any]]:
        groups = self.by_exchange()
        return [
            {"name": exchange, "count": len(stocks)}
            for exchange, stocks in sorted(groups.items(), key=lambda x: -len(x[1]))
        ]


def _ms(
    ticker: str,
    name: str,
    sector: str,
    industry: str = "",
    country: str = "US",
    exchange: str = "NYSE",
    currency: str = "USD",
    cap: str = "large",
    volume: int = 1_000_000,
    liquid: bool = True,
) -> StockMetadata:
    return StockMetadata(
        ticker=ticker,
        company_name=name,
        sector=sector,
        industry=industry or sector,
        country=country,
        exchange=exchange,
        currency=currency,
        market_cap_category=cap,
        avg_volume=volume,
        is_liquid=liquid,
    )


DEFAULT_UNIVERSE_STOCKS: list[StockMetadata] = [
    # === Technology ===
    _ms("AAPL", "Apple Inc.", "Technology", "Consumer Electronics", volume=50_000_000),
    _ms("MSFT", "Microsoft Corp.", "Technology", "Software", volume=30_000_000),
    _ms("NVDA", "NVIDIA Corp.", "Technology", "Semiconductors", volume=60_000_000),
    _ms("GOOGL", "Alphabet Inc.", "Technology", "Internet Services", volume=25_000_000),
    _ms("GOOG", "Alphabet Inc. (C)", "Technology", "Internet Services", volume=20_000_000),
    _ms("AMZN", "Amazon.com Inc.", "Technology", "E-Commerce", volume=40_000_000),
    _ms("META", "Meta Platforms Inc.", "Technology", "Social Media", volume=20_000_000),
    _ms("TSLA", "Tesla Inc.", "Technology", "Automotive", volume=80_000_000),
    _ms("AVGO", "Broadcom Inc.", "Technology", "Semiconductors", volume=25_000_000),
    _ms("ADBE", "Adobe Inc.", "Technology", "Software", volume=5_000_000),
    _ms("CRM", "Salesforce Inc.", "Technology", "Software", volume=8_000_000),
    _ms("INTC", "Intel Corp.", "Technology", "Semiconductors", volume=30_000_000),
    _ms("AMD", "Advanced Micro Devices", "Technology", "Semiconductors", volume=40_000_000),
    _ms("CSCO", "Cisco Systems Inc.", "Technology", "Networking", volume=15_000_000),
    _ms("ORCL", "Oracle Corp.", "Technology", "Software", volume=10_000_000),
    _ms("IBM", "IBM Corp.", "Technology", "IT Services", volume=5_000_000),
    _ms("QCOM", "Qualcomm Inc.", "Technology", "Semiconductors", volume=10_000_000),
    _ms("TXN", "Texas Instruments", "Technology", "Semiconductors", volume=6_000_000),
    _ms("NOW", "ServiceNow Inc.", "Technology", "Software", volume=2_000_000),
    _ms("MU", "Micron Technology", "Technology", "Semiconductors", volume=15_000_000),
    _ms("INTU", "Intuit Inc.", "Technology", "Software", volume=3_000_000),
    _ms("AMAT", "Applied Materials", "Technology", "Semiconductor Equipment", volume=5_000_000),
    _ms("PANW", "Palo Alto Networks", "Technology", "Cybersecurity", volume=4_000_000),
    _ms("CRWD", "CrowdStrike Holdings", "Technology", "Cybersecurity", volume=5_000_000),
    _ms("SNOW", "Snowflake Inc.", "Technology", "Data & Analytics", volume=5_000_000),
    _ms("PLTR", "Palantir Technologies", "Technology", "Data & Analytics", volume=30_000_000),
    _ms("MRVL", "Marvell Technology", "Technology", "Semiconductors", volume=10_000_000),
    _ms("KLAC", "KLA Corp.", "Technology", "Semiconductor Equipment", volume=2_000_000),
    _ms("LRCX", "Lam Research Corp.", "Technology", "Semiconductor Equipment", volume=2_000_000),
    _ms("ADI", "Analog Devices Inc.", "Technology", "Semiconductors", volume=4_000_000),
    _ms("DELL", "Dell Technologies", "Technology", "Computer Hardware", volume=5_000_000),
    _ms("HPQ", "HP Inc.", "Technology", "Computer Hardware", volume=6_000_000),
    _ms("WDC", "Western Digital Corp.", "Technology", "Data Storage", volume=4_000_000),
    _ms("STX", "Seagate Technology", "Technology", "Data Storage", volume=3_000_000),
    _ms("ANET", "Arista Networks", "Technology", "Networking", volume=2_000_000),
    _ms("SMCI", "Super Micro Computer", "Technology", "Computer Hardware", volume=15_000_000),
    _ms("DDOG", "Datadog Inc.", "Technology", "Software", volume=5_000_000),
    _ms("ZS", "Zscaler Inc.", "Technology", "Cybersecurity", volume=3_000_000),
    _ms("MDB", "MongoDB Inc.", "Technology", "Data & Analytics", volume=2_000_000),
    _ms("NET", "Cloudflare Inc.", "Technology", "Internet Services", volume=5_000_000),
    _ms("WDAY", "Workday Inc.", "Technology", "Software", volume=2_000_000),
    _ms("TEAM", "Atlassian Corp.", "Technology", "Software", volume=3_000_000),
    _ms("FTNT", "Fortinet Inc.", "Technology", "Cybersecurity", volume=5_000_000),
    _ms("APH", "Amphenol Corp.", "Technology", "Electronic Components", volume=3_000_000),
    _ms("GLW", "Corning Inc.", "Technology", "Electronic Components", volume=5_000_000),
    _ms("CDNS", "Cadence Design Systems", "Technology", "Software", volume=2_000_000),
    _ms("SNPS", "Synopsys Inc.", "Technology", "Software", volume=1_500_000),
    _ms("KEYS", "Keysight Technologies", "Technology", "Test & Measurement", volume=1_500_000),
    _ms("ROP", "Roper Technologies", "Technology", "Software", volume=1_000_000),
    _ms("TYL", "Tyler Technologies", "Technology", "Software", volume=400_000),
    _ms("VRSN", "VeriSign Inc.", "Technology", "Internet Services", volume=1_000_000),
    _ms("AKAM", "Akamai Technologies", "Technology", "Internet Services", volume=2_000_000),
    # === Technology - International ===
    _ms("SAP", "SAP SE", "Technology", "Software", country="Germany", exchange="XETRA", currency="EUR", volume=3_000_000),
    _ms("ASML", "ASML Holding NV", "Technology", "Semiconductor Equipment", country="Netherlands", exchange="Euronext", currency="EUR", volume=1_500_000),
    _ms("NXPI", "NXP Semiconductors", "Technology", "Semiconductors", country="Netherlands", exchange="Euronext", currency="EUR", volume=3_000_000),
    _ms("STM", "STMicroelectronics", "Technology", "Semiconductors", country="Switzerland", exchange="Euronext", currency="EUR", volume=4_000_000),
    _ms("IFNNY", "Infineon Technologies", "Technology", "Semiconductors", country="Germany", exchange="XETRA", currency="EUR", volume=1_000_000),
    _ms("SHOP", "Shopify Inc.", "Technology", "E-Commerce", country="Canada", exchange="TSX", currency="CAD", volume=5_000_000),
    _ms("CPFH.F", "Canopy Growth", "Technology", "Software", country="Canada", exchange="TSX", currency="CAD", volume=500_000, liquid=False),
    # === Healthcare ===
    _ms("UNH", "UnitedHealth Group", "Healthcare", "Health Insurance", volume=5_000_000),
    _ms("JNJ", "Johnson & Johnson", "Healthcare", "Pharmaceuticals", volume=8_000_000),
    _ms("LLY", "Eli Lilly & Co.", "Healthcare", "Pharmaceuticals", volume=5_000_000),
    _ms("PFE", "Pfizer Inc.", "Healthcare", "Pharmaceuticals", volume=20_000_000),
    _ms("ABBV", "AbbVie Inc.", "Healthcare", "Pharmaceuticals", volume=7_000_000),
    _ms("MRK", "Merck & Co.", "Healthcare", "Pharmaceuticals", volume=10_000_000),
    _ms("TMO", "Thermo Fisher Scientific", "Healthcare", "Life Sciences Tools", volume=2_000_000),
    _ms("ABT", "Abbott Laboratories", "Healthcare", "Medical Devices", volume=6_000_000),
    _ms("DHR", "Danaher Corp.", "Healthcare", "Life Sciences Tools", volume=2_000_000),
    _ms("MDT", "Medtronic PLC", "Healthcare", "Medical Devices", volume=6_000_000),
    _ms("BMY", "Bristol-Myers Squibb", "Healthcare", "Pharmaceuticals", volume=10_000_000),
    _ms("AMGN", "Amgen Inc.", "Healthcare", "Biotechnology", volume=4_000_000),
    _ms("GILD", "Gilead Sciences", "Healthcare", "Biotechnology", volume=7_000_000),
    _ms("ISRG", "Intuitive Surgical", "Healthcare", "Medical Devices", volume=1_500_000),
    _ms("SYK", "Stryker Corp.", "Healthcare", "Medical Devices", volume=2_000_000),
    _ms("VRTX", "Vertex Pharmaceuticals", "Healthcare", "Biotechnology", volume=2_000_000),
    _ms("REGN", "Regeneron Pharmaceuticals", "Healthcare", "Biotechnology", volume=1_000_000),
    _ms("BSX", "Boston Scientific Corp.", "Healthcare", "Medical Devices", volume=5_000_000),
    _ms("ZTS", "Zoetis Inc.", "Healthcare", "Animal Health", volume=2_000_000),
    _ms("IDXX", "IDEXX Laboratories", "Healthcare", "Diagnostics", volume=500_000),
    _ms("DXCM", "Dexcom Inc.", "Healthcare", "Medical Devices", volume=3_000_000),
    _ms("ALGN", "Align Technology", "Healthcare", "Medical Devices", volume=1_500_000),
    _ms("HOLX", "Hologic Inc.", "Healthcare", "Medical Devices", volume=2_000_000),
    _ms("ILMN", "Illumina Inc.", "Healthcare", "Life Sciences Tools", volume=2_000_000),
    _ms("BIIB", "Biogen Inc.", "Healthcare", "Biotechnology", volume=2_000_000),
    _ms("MRNA", "Moderna Inc.", "Healthcare", "Biotechnology", volume=10_000_000),
    _ms("CNC", "Centene Corp.", "Healthcare", "Health Insurance", volume=3_000_000),
    _ms("HUM", "Humana Inc.", "Healthcare", "Health Insurance", volume=1_500_000),
    _ms("CI", "Cigna Group", "Healthcare", "Health Insurance", volume=2_000_000),
    _ms("ELV", "Elevance Health Inc.", "Healthcare", "Health Insurance", volume=1_500_000),
    _ms("A", "Agilent Technologies", "Healthcare", "Life Sciences Tools", volume=2_000_000),
    _ms("WST", "West Pharmaceutical", "Healthcare", "Medical Devices", volume=500_000),
    _ms("RMD", "ResMed Inc.", "Healthcare", "Medical Devices", volume=1_500_000),
    _ms("PODD", "Insulet Corp.", "Healthcare", "Medical Devices", volume=1_000_000),
    # === Healthcare - International ===
    _ms("NVO", "Novo Nordisk A/S", "Healthcare", "Pharmaceuticals", country="Denmark", exchange="NYSE", currency="USD", volume=5_000_000),
    _ms("AZN", "AstraZeneca PLC", "Healthcare", "Pharmaceuticals", country="UK", exchange="NASDAQ", currency="USD", volume=5_000_000),
    _ms("SNY", "Sanofi SA", "Healthcare", "Pharmaceuticals", country="France", exchange="NASDAQ", currency="USD", volume=2_000_000),
    _ms("GSK", "GSK PLC", "Healthcare", "Pharmaceuticals", country="UK", exchange="NYSE", currency="USD", volume=3_000_000),
    _ms("NVS", "Novartis AG", "Healthcare", "Pharmaceuticals", country="Switzerland", exchange="NYSE", currency="USD", volume=2_000_000),
    _ms("RHHBY", "Roche Holding AG", "Healthcare", "Pharmaceuticals", country="Switzerland", exchange="OTC", currency="USD", volume=500_000),
    # === Financial Services ===
    _ms("JPM", "JPMorgan Chase & Co.", "Financial Services", "Banking", volume=10_000_000),
    _ms("BAC", "Bank of America Corp.", "Financial Services", "Banking", volume=25_000_000),
    _ms("WFC", "Wells Fargo & Co.", "Financial Services", "Banking", volume=15_000_000),
    _ms("C", "Citigroup Inc.", "Financial Services", "Banking", volume=12_000_000),
    _ms("GS", "Goldman Sachs Group", "Financial Services", "Investment Banking", volume=2_000_000),
    _ms("MS", "Morgan Stanley", "Financial Services", "Investment Banking", volume=7_000_000),
    _ms("V", "Visa Inc.", "Financial Services", "Payment Processing", volume=8_000_000),
    _ms("MA", "Mastercard Inc.", "Financial Services", "Payment Processing", volume=5_000_000),
    _ms("PYPL", "PayPal Holdings Inc.", "Financial Services", "Payment Processing", volume=10_000_000),
    _ms("BLK", "BlackRock Inc.", "Financial Services", "Asset Management", volume=1_000_000),
    _ms("SCHW", "Charles Schwab Corp.", "Financial Services", "Brokerage", volume=8_000_000),
    _ms("AXP", "American Express Co.", "Financial Services", "Consumer Finance", volume=4_000_000),
    _ms("COF", "Capital One Financial", "Financial Services", "Consumer Finance", volume=3_000_000),
    _ms("USB", "U.S. Bancorp", "Financial Services", "Banking", volume=8_000_000),
    _ms("PNC", "PNC Financial Services", "Financial Services", "Banking", volume=2_000_000),
    _ms("TFC", "Truist Financial Corp.", "Financial Services", "Banking", volume=7_000_000),
    _ms("BK", "Bank of New York Mellon", "Financial Services", "Custody Banking", volume=3_000_000),
    _ms("KKR", "KKR & Co. Inc.", "Financial Services", "Investment Management", volume=2_000_000),
    _ms("APO", "Apollo Global Management", "Financial Services", "Investment Management", volume=2_000_000),
    _ms("BX", "Blackstone Inc.", "Financial Services", "Investment Management", volume=3_000_000),
    _ms("CME", "CME Group Inc.", "Financial Services", "Financial Exchanges", volume=2_000_000),
    _ms("ICE", "Intercontinental Exchange", "Financial Services", "Financial Exchanges", volume=2_000_000),
    _ms("MCO", "Moody's Corp.", "Financial Services", "Financial Data", volume=1_000_000),
    _ms("SPGI", "S&P Global Inc.", "Financial Services", "Financial Data", volume=2_000_000),
    _ms("MSCI", "MSCI Inc.", "Financial Services", "Financial Data", volume=1_000_000),
    _ms("FISV", "Fiserv Inc.", "Financial Services", "Fintech", volume=3_000_000),
    _ms("FIS", "Fidelity National Info.", "Financial Services", "Fintech", volume=3_000_000),
    _ms("SQ", "Block Inc.", "Financial Services", "Fintech", volume=10_000_000),
    _ms("HOOD", "Robinhood Markets", "Financial Services", "Brokerage", volume=10_000_000),
    # === Financial - International ===
    _ms("HSBC", "HSBC Holdings PLC", "Financial Services", "Banking", country="UK", exchange="NYSE", currency="USD", volume=3_000_000),
    _ms("BARC", "Barclays PLC", "Financial Services", "Banking", country="UK", exchange="LSE", currency="GBP", volume=10_000_000),
    _ms("DBK.DE", "Deutsche Bank AG", "Financial Services", "Banking", country="Germany", exchange="XETRA", currency="EUR", volume=5_000_000),
    _ms("UBSG.SW", "UBS Group AG", "Financial Services", "Banking", country="Switzerland", exchange="SIX", currency="CHF", volume=3_000_000),
    _ms("RY", "Royal Bank of Canada", "Financial Services", "Banking", country="Canada", exchange="TSX", currency="CAD", volume=3_000_000),
    _ms("TD", "Toronto-Dominion Bank", "Financial Services", "Banking", country="Canada", exchange="TSX", currency="CAD", volume=4_000_000),
    _ms("BNS", "Bank of Nova Scotia", "Financial Services", "Banking", country="Canada", exchange="TSX", currency="CAD", volume=3_000_000),
    _ms("MUFG", "Mitsubishi UFJ FG", "Financial Services", "Banking", country="Japan", exchange="NYSE", currency="USD", volume=1_000_000),
    _ms("SMFG", "Sumitomo Mitsui FG", "Financial Services", "Banking", country="Japan", exchange="NYSE", currency="USD", volume=500_000),
    # === Energy ===
    _ms("XOM", "Exxon Mobil Corp.", "Energy", "Oil & Gas Integrated", volume=20_000_000),
    _ms("CVX", "Chevron Corp.", "Energy", "Oil & Gas Integrated", volume=10_000_000),
    _ms("COP", "ConocoPhillips", "Energy", "Oil & Gas E&P", volume=5_000_000),
    _ms("EOG", "EOG Resources Inc.", "Energy", "Oil & Gas E&P", volume=4_000_000),
    _ms("SLB", "Schlumberger NV", "Energy", "Oil & Gas Services", volume=7_000_000),
    _ms("HAL", "Halliburton Co.", "Energy", "Oil & Gas Services", volume=5_000_000),
    _ms("OXY", "Occidental Petroleum", "Energy", "Oil & Gas E&P", volume=10_000_000),
    _ms("MPC", "Marathon Petroleum Corp.", "Energy", "Oil & Gas Refining", volume=3_000_000),
    _ms("PSX", "Phillips 66", "Energy", "Oil & Gas Refining", volume=3_000_000),
    _ms("VLO", "Valero Energy Corp.", "Energy", "Oil & Gas Refining", volume=3_000_000),
    _ms("KMI", "Kinder Morgan Inc.", "Energy", "Oil & Gas Midstream", volume=8_000_000),
    _ms("WMB", "Williams Companies", "Energy", "Oil & Gas Midstream", volume=5_000_000),
    _ms("OKE", "ONEOK Inc.", "Energy", "Oil & Gas Midstream", volume=3_000_000),
    _ms("DUK", "Duke Energy Corp.", "Energy", "Electric Utilities", volume=3_000_000),
    _ms("NEE", "NextEra Energy Inc.", "Energy", "Renewable Energy", volume=8_000_000),
    _ms("CEG", "Constellation Energy", "Energy", "Electric Utilities", volume=5_000_000),
    _ms("ENPH", "Enphase Energy Inc.", "Energy", "Renewable Energy", volume=5_000_000),
    _ms("SEDG", "SolarEdge Technologies", "Energy", "Renewable Energy", volume=2_000_000),
    _ms("TTE", "TotalEnergies SE", "Energy", "Oil & Gas Integrated", country="France", exchange="NYSE", currency="USD", volume=3_000_000),
    _ms("BP", "BP PLC", "Energy", "Oil & Gas Integrated", country="UK", exchange="NYSE", currency="USD", volume=8_000_000),
    _ms("SHEL", "Shell PLC", "Energy", "Oil & Gas Integrated", country="UK", exchange="NYSE", currency="USD", volume=5_000_000),
    _ms("EQNR", "Equinor ASA", "Energy", "Oil & Gas Integrated", country="Norway", exchange="NYSE", currency="USD", volume=2_000_000),
    _ms("CNQ", "Canadian Natural Resources", "Energy", "Oil & Gas E&P", country="Canada", exchange="TSX", currency="CAD", volume=3_000_000),
    _ms("SU", "Suncor Energy Inc.", "Energy", "Oil & Gas Integrated", country="Canada", exchange="TSX", currency="CAD", volume=5_000_000),
    # === Consumer Cyclical ===
    _ms("AMZN", "Amazon.com Inc.", "Consumer Cyclical", "E-Commerce", volume=40_000_000),
    _ms("TSLA", "Tesla Inc.", "Consumer Cyclical", "Automotive", volume=80_000_000),
    _ms("HD", "Home Depot Inc.", "Consumer Cyclical", "Home Improvement", volume=5_000_000),
    _ms("MCD", "McDonald's Corp.", "Consumer Cyclical", "Restaurants", volume=4_000_000),
    _ms("NKE", "Nike Inc.", "Consumer Cyclical", "Apparel & Footwear", volume=7_000_000),
    _ms("SBUX", "Starbucks Corp.", "Consumer Cyclical", "Restaurants", volume=8_000_000),
    _ms("LOW", "Lowe's Companies", "Consumer Cyclical", "Home Improvement", volume=3_000_000),
    _ms("TJX", "TJX Companies Inc.", "Consumer Cyclical", "Apparel Retail", volume=5_000_000),
    _ms("BKNG", "Booking Holdings Inc.", "Consumer Cyclical", "Travel Services", volume=500_000),
    _ms("MAR", "Marriott International", "Consumer Cyclical", "Hotels", volume=3_000_000),
    _ms("HLT", "Hilton Worldwide", "Consumer Cyclical", "Hotels", volume=2_000_000),
    _ms("ABNB", "Airbnb Inc.", "Consumer Cyclical", "Travel Services", volume=5_000_000),
    _ms("GM", "General Motors Co.", "Consumer Cyclical", "Automotive", volume=10_000_000),
    _ms("F", "Ford Motor Co.", "Consumer Cyclical", "Automotive", volume=30_000_000),
    _ms("RIVN", "Rivian Automotive", "Consumer Cyclical", "Automotive", volume=15_000_000),
    _ms("LCID", "Lucid Group Inc.", "Consumer Cyclical", "Automotive", volume=15_000_000, liquid=False),
    _ms("DRI", "Darden Restaurants", "Consumer Cyclical", "Restaurants", volume=2_000_000),
    _ms("YUM", "Yum! Brands Inc.", "Consumer Cyclical", "Restaurants", volume=2_000_000),
    _ms("CMG", "Chipotle Mexican Grill", "Consumer Cyclical", "Restaurants", volume=1_000_000),
    _ms("ROST", "Ross Stores Inc.", "Consumer Cyclical", "Apparel Retail", volume=3_000_000),
    _ms("TGT", "Target Corp.", "Consumer Cyclical", "Big Box Retail", volume=5_000_000),
    _ms("BBY", "Best Buy Co.", "Consumer Cyclical", "Consumer Electronics Retail", volume=3_000_000),
    _ms("GPC", "Genuine Parts Co.", "Consumer Cyclical", "Auto Parts", volume=1_000_000),
    _ms("EBAY", "eBay Inc.", "Consumer Cyclical", "E-Commerce", volume=5_000_000),
    _ms("ETSY", "Etsy Inc.", "Consumer Cyclical", "E-Commerce", volume=4_000_000),
    # === Consumer Defensive ===
    _ms("PG", "Procter & Gamble Co.", "Consumer Defensive", "Household Products", volume=8_000_000),
    _ms("KO", "Coca-Cola Co.", "Consumer Defensive", "Beverages", volume=15_000_000),
    _ms("PEP", "PepsiCo Inc.", "Consumer Defensive", "Beverages & Snacks", volume=6_000_000),
    _ms("WMT", "Walmart Inc.", "Consumer Defensive", "Big Box Retail", volume=8_000_000),
    _ms("COST", "Costco Wholesale Corp.", "Consumer Defensive", "Warehouse Retail", volume=3_000_000),
    _ms("MO", "Altria Group Inc.", "Consumer Defensive", "Tobacco", volume=7_000_000),
    _ms("PM", "Philip Morris International", "Consumer Defensive", "Tobacco", volume=5_000_000),
    _ms("CL", "Colgate-Palmolive Co.", "Consumer Defensive", "Household Products", volume=3_000_000),
    _ms("KMB", "Kimberly-Clark Corp.", "Consumer Defensive", "Household Products", volume=2_000_000),
    _ms("KHC", "Kraft Heinz Co.", "Consumer Defensive", "Packaged Foods", volume=6_000_000),
    _ms("MDLZ", "Mondelez International", "Consumer Defensive", "Snacks", volume=5_000_000),
    _ms("CAG", "Conagra Brands Inc.", "Consumer Defensive", "Packaged Foods", volume=4_000_000),
    _ms("SJM", "JM Smucker Co.", "Consumer Defensive", "Packaged Foods", volume=1_500_000),
    _ms("CPB", "Campbell's Co.", "Consumer Defensive", "Packaged Foods", volume=2_000_000),
    _ms("GIS", "General Mills Inc.", "Consumer Defensive", "Packaged Foods", volume=3_000_000),
    _ms("K", "Kellanova", "Consumer Defensive", "Cereal & Snacks", volume=2_000_000),
    _ms("HRL", "Hormel Foods Corp.", "Consumer Defensive", "Packaged Meats", volume=3_000_000),
    _ms("TAP", "Molson Coors Beverage", "Consumer Defensive", "Beverages", volume=2_000_000),
    _ms("STZ", "Constellation Brands", "Consumer Defensive", "Beverages", volume=2_000_000),
    _ms("MNST", "Monster Beverage Corp.", "Consumer Defensive", "Beverages", volume=5_000_000),
    _ms("CLX", "Clorox Co.", "Consumer Defensive", "Household Products", volume=2_000_000),
    _ms("CHD", "Church & Dwight Co.", "Consumer Defensive", "Household Products", volume=2_000_000),
    _ms("EL", "Estee Lauder Companies", "Consumer Defensive", "Personal Care", volume=3_000_000),
    _ms("UL", "Unilever PLC", "Consumer Defensive", "Personal Care", country="UK", exchange="NYSE", currency="USD", volume=3_000_000),
    _ms("NSRGY", "Nestle SA", "Consumer Defensive", "Packaged Foods", country="Switzerland", exchange="OTC", currency="USD", volume=500_000),
    # === Industrials ===
    _ms("CAT", "Caterpillar Inc.", "Industrials", "Construction & Mining", volume=3_000_000),
    _ms("DE", "Deere & Co.", "Industrials", "Farm Machinery", volume=2_000_000),
    _ms("GE", "General Electric Co.", "Industrials", "Diversified Industrials", volume=10_000_000),
    _ms("HON", "Honeywell International", "Industrials", "Diversified Industrials", volume=4_000_000),
    _ms("UPS", "United Parcel Service", "Industrials", "Logistics", volume=5_000_000),
    _ms("FDX", "FedEx Corp.", "Industrials", "Logistics", volume=2_000_000),
    _ms("BA", "Boeing Co.", "Industrials", "Aerospace & Defense", volume=7_000_000),
    _ms("RTX", "RTX Corp.", "Industrials", "Aerospace & Defense", volume=5_000_000),
    _ms("LMT", "Lockheed Martin Corp.", "Industrials", "Aerospace & Defense", volume=2_000_000),
    _ms("GD", "General Dynamics Corp.", "Industrials", "Aerospace & Defense", volume=1_500_000),
    _ms("NOC", "Northrop Grumman Corp.", "Industrials", "Aerospace & Defense", volume=1_000_000),
    _ms("MMM", "3M Co.", "Industrials", "Diversified Industrials", volume=4_000_000),
    _ms("EMR", "Emerson Electric Co.", "Industrials", "Industrial Automation", volume=3_000_000),
    _ms("ETN", "Eaton Corp. PLC", "Industrials", "Electrical Equipment", volume=2_000_000),
    _ms("CMI", "Cummins Inc.", "Industrials", "Industrial Engines", volume=1_000_000),
    _ms("CSX", "CSX Corp.", "Industrials", "Railroads", volume=5_000_000),
    _ms("NSC", "Norfolk Southern Corp.", "Industrials", "Railroads", volume=2_000_000),
    _ms("UNP", "Union Pacific Corp.", "Industrials", "Railroads", volume=2_000_000),
    _ms("WM", "Waste Management Inc.", "Industrials", "Waste Services", volume=2_000_000),
    _ms("RSG", "Republic Services Inc.", "Industrials", "Waste Services", volume=1_500_000),
    _ms("CHRW", "C.H. Robinson Worldwide", "Industrials", "Logistics", volume=1_500_000),
    _ms("JBHT", "J.B. Hunt Transport", "Industrials", "Trucking", volume=1_000_000),
    _ms("PAYX", "Paychex Inc.", "Industrials", "HR & Payroll", volume=2_000_000),
    _ms("CTAS", "Cintas Corp.", "Industrials", "Uniform Rental", volume=1_000_000),
    _ms("ROK", "Rockwell Automation", "Industrials", "Industrial Automation", volume=1_000_000),
    _ms("IR", "Ingersoll Rand Inc.", "Industrials", "Industrial Machinery", volume=2_000_000),
    _ms("DOV", "Dover Corp.", "Industrials", "Industrial Machinery", volume=1_500_000),
    _ms("PH", "Parker-Hannifin Corp.", "Industrials", "Industrial Machinery", volume=1_000_000),
    _ms("OTIS", "Otis Worldwide Corp.", "Industrials", "Elevator Manufacturing", volume=2_000_000),
    _ms("CARR", "Carrier Global Corp.", "Industrials", "HVAC", volume=3_000_000),
    _ms("FAST", "Fastenal Co.", "Industrials", "Industrial Distribution", volume=3_000_000),
    _ms("GWW", "W.W. Grainger Inc.", "Industrials", "Industrial Distribution", volume=500_000),
    # === Industrials - International ===
    _ms("SIEGY", "Siemens AG", "Industrials", "Diversified Industrials", country="Germany", exchange="OTC", currency="USD", volume=300_000),
    _ms("AIR.PA", "Airbus SE", "Industrials", "Aerospace & Defense", country="France", exchange="Euronext", currency="EUR", volume=1_500_000),
    # === Communication Services ===
    _ms("META", "Meta Platforms Inc.", "Communication Services", "Social Media", volume=20_000_000),
    _ms("GOOGL", "Alphabet Inc.", "Communication Services", "Internet Services", volume=25_000_000),
    _ms("NFLX", "Netflix Inc.", "Communication Services", "Entertainment", volume=5_000_000),
    _ms("DIS", "Walt Disney Co.", "Communication Services", "Entertainment", volume=10_000_000),
    _ms("CMCSA", "Comcast Corp.", "Communication Services", "Telecommunications", volume=15_000_000),
    _ms("VZ", "Verizon Communications", "Communication Services", "Telecommunications", volume=20_000_000),
    _ms("T", "AT&T Inc.", "Communication Services", "Telecommunications", volume=25_000_000),
    _ms("TMUS", "T-Mobile US Inc.", "Communication Services", "Telecommunications", volume=5_000_000),
    _ms("CHTR", "Charter Communications", "Communication Services", "Telecommunications", volume=2_000_000),
    _ms("WBD", "Warner Bros. Discovery", "Communication Services", "Entertainment", volume=15_000_000),
    _ms("LYV", "Live Nation Entertainment", "Communication Services", "Entertainment", volume=3_000_000),
    _ms("ROKU", "Roku Inc.", "Communication Services", "Entertainment", volume=4_000_000),
    _ms("SPOT", "Spotify Technology SA", "Communication Services", "Music Streaming", country="Sweden", exchange="NYSE", currency="USD", volume=3_000_000),
    _ms("SNAP", "Snap Inc.", "Communication Services", "Social Media", volume=15_000_000),
    _ms("PINS", "Pinterest Inc.", "Communication Services", "Social Media", volume=5_000_000),
    _ms("TTWO", "Take-Two Interactive", "Communication Services", "Gaming", volume=2_000_000),
    _ms("EA", "Electronic Arts Inc.", "Communication Services", "Gaming", volume=3_000_000),
    _ms("OMC", "Omnicom Group", "Communication Services", "Advertising", volume=1_500_000),
    _ms("IPG", "Interpublic Group", "Communication Services", "Advertising", volume=3_000_000),
    _ms("TME", "Tencent Music Ent.", "Communication Services", "Entertainment", country="China", exchange="NYSE", currency="USD", volume=5_000_000),
    # === Materials ===
    _ms("LIN", "Linde PLC", "Materials", "Industrial Gases", volume=2_000_000),
    _ms("APD", "Air Products & Chemicals", "Materials", "Industrial Gases", volume=1_500_000),
    _ms("SHW", "Sherwin-Williams Co.", "Materials", "Paints & Coatings", volume=1_500_000),
    _ms("ECL", "Ecolab Inc.", "Materials", "Specialty Chemicals", volume=1_500_000),
    _ms("NEM", "Newmont Corp.", "Materials", "Gold Mining", volume=8_000_000),
    _ms("FCX", "Freeport-McMoRan Inc.", "Materials", "Copper Mining", volume=8_000_000),
    _ms("DOW", "Dow Inc.", "Materials", "Commodity Chemicals", volume=5_000_000),
    _ms("DD", "DuPont de Nemours Inc.", "Materials", "Specialty Chemicals", volume=3_000_000),
    _ms("PPG", "PPG Industries Inc.", "Materials", "Paints & Coatings", volume=2_000_000),
    _ms("LYB", "LyondellBasell Industries", "Materials", "Commodity Chemicals", volume=3_000_000),
    _ms("BLL", "Ball Corp.", "Materials", "Metal Packaging", volume=2_000_000),
    _ms("IP", "International Paper Co.", "Materials", "Paper & Packaging", volume=3_000_000),
    _ms("PKG", "Packaging Corp. of America", "Materials", "Paper & Packaging", volume=1_000_000),
    _ms("WRK", "WestRock Co.", "Materials", "Paper & Packaging", volume=2_000_000),
    _ms("CF", "CF Industries Holdings", "Materials", "Fertilizers", volume=2_000_000),
    _ms("MOS", "Mosaic Co.", "Materials", "Fertilizers", volume=3_000_000),
    _ms("FMC", "FMC Corp.", "Materials", "Agricultural Chemicals", volume=1_500_000),
    _ms("ALB", "Albemarle Corp.", "Materials", "Lithium & Specialty Chemicals", volume=3_000_000),
    # === Real Estate ===
    _ms("PLD", "Prologis Inc.", "Real Estate", "Industrial REIT", volume=4_000_000),
    _ms("AMT", "American Tower Corp.", "Real Estate", "Infrastructure REIT", volume=3_000_000),
    _ms("CCI", "Crown Castle Inc.", "Real Estate", "Infrastructure REIT", volume=3_000_000),
    _ms("EQIX", "Equinix Inc.", "Real Estate", "Data Center REIT", volume=1_000_000),
    _ms("DLR", "Digital Realty Trust", "Real Estate", "Data Center REIT", volume=2_000_000),
    _ms("PSA", "Public Storage", "Real Estate", "Self-Storage REIT", volume=1_500_000),
    _ms("O", "Realty Income Corp.", "Real Estate", "Retail REIT", volume=4_000_000),
    _ms("SPG", "Simon Property Group", "Real Estate", "Mall REIT", volume=2_000_000),
    _ms("WELL", "Welltower Inc.", "Real Estate", "Healthcare REIT", volume=2_000_000),
    _ms("AVB", "AvalonBay Communities", "Real Estate", "Apartment REIT", volume=1_000_000),
    _ms("EQR", "Equity Residential", "Real Estate", "Apartment REIT", volume=2_000_000),
    _ms("VICI", "VICI Properties", "Real Estate", "Gaming REIT", volume=3_000_000),
    _ms("INVH", "Invitation Homes Inc.", "Real Estate", "Single-Family Rental", volume=3_000_000),
    # === Utilities ===
    _ms("NEE", "NextEra Energy Inc.", "Utilities", "Electric Utilities", volume=8_000_000),
    _ms("DUK", "Duke Energy Corp.", "Utilities", "Electric Utilities", volume=3_000_000),
    _ms("SO", "Southern Co.", "Utilities", "Electric Utilities", volume=4_000_000),
    _ms("D", "Dominion Energy Inc.", "Utilities", "Electric Utilities", volume=3_000_000),
    _ms("AEP", "American Electric Power", "Utilities", "Electric Utilities", volume=3_000_000),
    _ms("EXC", "Exelon Corp.", "Utilities", "Electric Utilities", volume=5_000_000),
    _ms("SRE", "Sempra Energy", "Utilities", "Electric & Gas Utilities", volume=2_000_000),
    _ms("XEL", "Xcel Energy Inc.", "Utilities", "Electric Utilities", volume=3_000_000),
    _ms("ED", "Consolidated Edison", "Utilities", "Electric Utilities", volume=2_000_000),
    _ms("PEG", "Public Service Enterprise", "Utilities", "Electric Utilities", volume=2_000_000),
    _ms("WEC", "WEC Energy Group", "Utilities", "Electric Utilities", volume=2_000_000),
    _ms("AWK", "American Water Works", "Utilities", "Water Utilities", volume=1_500_000),
    _ms("EIX", "Edison International", "Utilities", "Electric Utilities", volume=2_000_000),
    # === ETFs (Broad Market) ===
    _ms("SPY", "SPDR S&P 500 ETF", "ETF", "Broad Market ETF", volume=80_000_000),
    _ms("QQQ", "Invesco QQQ Trust", "ETF", "Technology ETF", volume=50_000_000),
    _ms("IWM", "iShares Russell 2000 ETF", "ETF", "Small Cap ETF", volume=25_000_000),
    _ms("DIA", "SPDR Dow Jones ETF", "ETF", "Broad Market ETF", volume=5_000_000),
    _ms("TLT", "iShares 20+ Year Treasury", "ETF", "Fixed Income ETF", volume=20_000_000),
    _ms("AGG", "iShares Core US Aggregate Bond", "ETF", "Fixed Income ETF", volume=10_000_000),
    _ms("GLD", "SPDR Gold Shares", "ETF", "Commodity ETF", volume=10_000_000),
    _ms("SLV", "iShares Silver Trust", "ETF", "Commodity ETF", volume=10_000_000),
    _ms("USO", "United States Oil Fund", "ETF", "Commodity ETF", volume=15_000_000),
    _ms("VTI", "Vanguard Total Stock Market", "ETF", "Broad Market ETF", volume=5_000_000),
    _ms("VXUS", "Vanguard Total International", "ETF", "International ETF", volume=5_000_000),
    _ms("BND", "Vanguard Total Bond Market", "ETF", "Fixed Income ETF", volume=5_000_000),
    _ms("XLF", "Financial Select Sector SPDR", "ETF", "Sector ETF", volume=25_000_000),
    _ms("XLK", "Technology Select Sector SPDR", "ETF", "Sector ETF", volume=15_000_000),
    _ms("XLV", "Health Care Select Sector SPDR", "ETF", "Sector ETF", volume=10_000_000),
    _ms("XLE", "Energy Select Sector SPDR", "ETF", "Sector ETF", volume=15_000_000),
    _ms("XLI", "Industrial Select Sector SPDR", "ETF", "Sector ETF", volume=8_000_000),
    _ms("XLP", "Consumer Staples Select Sector", "ETF", "Sector ETF", volume=5_000_000),
    _ms("XLY", "Consumer Discretionary Select", "ETF", "Sector ETF", volume=5_000_000),
    _ms("XLU", "Utilities Select Sector SPDR", "ETF", "Sector ETF", volume=10_000_000),
    _ms("XLB", "Materials Select Sector SPDR", "ETF", "Sector ETF", volume=5_000_000),
    _ms("XLRE", "Real Estate Select Sector SPDR", "ETF", "Sector ETF", volume=5_000_000),
    # === Japan (TSE) ===
    _ms("TM", "Toyota Motor Corp.", "Consumer Cyclical", "Automotive", country="Japan", exchange="NYSE", currency="USD", volume=500_000),
    _ms("SONY", "Sony Group Corp.", "Technology", "Consumer Electronics", country="Japan", exchange="NYSE", currency="USD", volume=2_000_000),
    _ms("HMC", "Honda Motor Co.", "Consumer Cyclical", "Automotive", country="Japan", exchange="NYSE", currency="USD", volume=1_000_000),
    _ms("MFG", "Mizuho Financial Group", "Financial Services", "Banking", country="Japan", exchange="NYSE", currency="USD", volume=500_000),
    # === China / Hong Kong ===
    _ms("BABA", "Alibaba Group Holding", "Consumer Cyclical", "E-Commerce", country="China", exchange="NYSE", currency="USD", volume=15_000_000),
    _ms("JD", "JD.com Inc.", "Consumer Cyclical", "E-Commerce", country="China", exchange="NASDAQ", currency="USD", volume=10_000_000),
    _ms("PDD", "PDD Holdings Inc.", "Consumer Cyclical", "E-Commerce", country="China", exchange="NASDAQ", currency="USD", volume=10_000_000),
    _ms("BIDU", "Baidu Inc.", "Technology", "Internet Services", country="China", exchange="NASDAQ", currency="USD", volume=5_000_000),
    _ms("NIO", "NIO Inc.", "Consumer Cyclical", "Automotive", country="China", exchange="NYSE", currency="USD", volume=25_000_000),
    _ms("LI", "Li Auto Inc.", "Consumer Cyclical", "Automotive", country="China", exchange="NASDAQ", currency="USD", volume=8_000_000),
    _ms("XPEV", "XPeng Inc.", "Consumer Cyclical", "Automotive", country="China", exchange="NYSE", currency="USD", volume=8_000_000),
    _ms("TCEHY", "Tencent Holdings Ltd.", "Technology", "Internet Services", country="China", exchange="OTC", currency="USD", volume=5_000_000),
    _ms("NTES", "NetEase Inc.", "Technology", "Internet Services", country="China", exchange="NASDAQ", currency="USD", volume=2_000_000),
    # === India (NSE) ===
    _ms("RELIANCE.NS", "Reliance Industries", "Energy", "Oil & Gas Integrated", country="India", exchange="NSE", currency="INR", volume=5_000_000),
    _ms("TCS.NS", "Tata Consultancy Services", "Technology", "Software", country="India", exchange="NSE", currency="INR", volume=2_000_000),
    _ms("HDB", "HDFC Bank Ltd.", "Financial Services", "Banking", country="India", exchange="NYSE", currency="USD", volume=3_000_000),
    _ms("IBN", "ICICI Bank Ltd.", "Financial Services", "Banking", country="India", exchange="NYSE", currency="USD", volume=3_000_000),
    _ms("INFY", "Infosys Ltd.", "Technology", "Software", country="India", exchange="NYSE", currency="USD", volume=5_000_000),
    # === Brazil ===
    _ms("PBR", "Petrobras SA", "Energy", "Oil & Gas Integrated", country="Brazil", exchange="NYSE", currency="USD", volume=5_000_000),
    _ms("VALE", "Vale SA", "Materials", "Mining", country="Brazil", exchange="NYSE", currency="USD", volume=10_000_000),
    _ms("ITUB", "Itau Unibanco Holding", "Financial Services", "Banking", country="Brazil", exchange="NYSE", currency="USD", volume=3_000_000),
    _ms("BBD", "Banco Bradesco SA", "Financial Services", "Banking", country="Brazil", exchange="NYSE", currency="USD", volume=4_000_000),
    # === Australia (ASX) ===
    _ms("BHP", "BHP Group Ltd.", "Materials", "Mining", country="Australia", exchange="NYSE", currency="USD", volume=3_000_000),
    _ms("RIO", "Rio Tinto PLC", "Materials", "Mining", country="Australia", exchange="NYSE", currency="USD", volume=2_000_000),
    _ms("CBA.AX", "Commonwealth Bank of Australia", "Financial Services", "Banking", country="Australia", exchange="ASX", currency="AUD", volume=2_000_000),
    _ms("WBC.AX", "Westpac Banking Corp.", "Financial Services", "Banking", country="Australia", exchange="ASX", currency="AUD", volume=3_000_000),
    _ms("NAB.AX", "National Australia Bank", "Financial Services", "Banking", country="Australia", exchange="ASX", currency="AUD", volume=2_000_000),
    _ms("ANZ.AX", "ANZ Group Holdings Ltd.", "Financial Services", "Banking", country="Australia", exchange="ASX", currency="AUD", volume=2_000_000),
    _ms("WOW.AX", "Woolworths Group Ltd.", "Consumer Defensive", "Food Retail", country="Australia", exchange="ASX", currency="AUD", volume=1_500_000),
    _ms("CSL.AX", "CSL Ltd.", "Healthcare", "Biotechnology", country="Australia", exchange="ASX", currency="AUD", volume=500_000),
    # === UK (LSE) ===
    _ms("ULVR.L", "Unilever PLC", "Consumer Defensive", "Personal Care", country="UK", exchange="LSE", currency="GBP", volume=3_000_000),
    _ms("AZN.L", "AstraZeneca PLC", "Healthcare", "Pharmaceuticals", country="UK", exchange="LSE", currency="GBP", volume=2_000_000),
    _ms("SHEL.L", "Shell PLC", "Energy", "Oil & Gas Integrated", country="UK", exchange="LSE", currency="GBP", volume=5_000_000),
    _ms("HSBA.L", "HSBC Holdings PLC", "Financial Services", "Banking", country="UK", exchange="LSE", currency="GBP", volume=8_000_000),
    _ms("BP.L", "BP PLC", "Energy", "Oil & Gas Integrated", country="UK", exchange="LSE", currency="GBP", volume=10_000_000),
    _ms("GSK.L", "GSK PLC", "Healthcare", "Pharmaceuticals", country="UK", exchange="LSE", currency="GBP", volume=4_000_000),
    _ms("LLOY.L", "Lloyds Banking Group", "Financial Services", "Banking", country="UK", exchange="LSE", currency="GBP", volume=30_000_000),
    _ms("BARC.L", "Barclays PLC", "Financial Services", "Banking", country="UK", exchange="LSE", currency="GBP", volume=15_000_000),
    _ms("RIO.L", "Rio Tinto PLC", "Materials", "Mining", country="UK", exchange="LSE", currency="GBP", volume=2_000_000),
    _ms("VOD.L", "Vodafone Group PLC", "Communication Services", "Telecommunications", country="UK", exchange="LSE", currency="GBP", volume=15_000_000),
    _ms("BT-A.L", "BT Group PLC", "Communication Services", "Telecommunications", country="UK", exchange="LSE", currency="GBP", volume=10_000_000),
    # === Germany (XETRA) ===
    _ms("SAP.DE", "SAP SE", "Technology", "Software", country="Germany", exchange="XETRA", currency="EUR", volume=2_000_000),
    _ms("DTE.DE", "Deutsche Telekom AG", "Communication Services", "Telecommunications", country="Germany", exchange="XETRA", currency="EUR", volume=5_000_000),
    _ms("ALV.DE", "Allianz SE", "Financial Services", "Insurance", country="Germany", exchange="XETRA", currency="EUR", volume=1_500_000),
    _ms("SIE.DE", "Siemens AG", "Industrials", "Diversified Industrials", country="Germany", exchange="XETRA", currency="EUR", volume=2_000_000),
    _ms("BAS.DE", "BASF SE", "Materials", "Commodity Chemicals", country="Germany", exchange="XETRA", currency="EUR", volume=2_000_000),
    _ms("BAYN.DE", "Bayer AG", "Healthcare", "Pharmaceuticals", country="Germany", exchange="XETRA", currency="EUR", volume=3_000_000),
    _ms("MBG.DE", "Mercedes-Benz Group", "Consumer Cyclical", "Automotive", country="Germany", exchange="XETRA", currency="EUR", volume=2_000_000),
    _ms("VOW3.DE", "Volkswagen AG", "Consumer Cyclical", "Automotive", country="Germany", exchange="XETRA", currency="EUR", volume=1_500_000),
    _ms("BMW.DE", "Bayerische Motoren Werke", "Consumer Cyclical", "Automotive", country="Germany", exchange="XETRA", currency="EUR", volume=1_500_000),
    # === France (Euronext) ===
    _ms("MC.PA", "LVMH Moet Hennessy", "Consumer Cyclical", "Luxury Goods", country="France", exchange="Euronext", currency="EUR", volume=500_000),
    _ms("OR.PA", "L'Oreal SA", "Consumer Defensive", "Personal Care", country="France", exchange="Euronext", currency="EUR", volume=500_000),
    _ms("SAN.PA", "Sanofi SA", "Healthcare", "Pharmaceuticals", country="France", exchange="Euronext", currency="EUR", volume=1_500_000),
    _ms("AIR.PA", "Airbus SE", "Industrials", "Aerospace & Defense", country="France", exchange="Euronext", currency="EUR", volume=1_500_000),
    _ms("ACA.PA", "Credit Agricole SA", "Financial Services", "Banking", country="France", exchange="Euronext", currency="EUR", volume=3_000_000),
    _ms("BNP.PA", "BNP Paribas SA", "Financial Services", "Banking", country="France", exchange="Euronext", currency="EUR", volume=2_000_000),
    _ms("ENGI.PA", "Engie SA", "Utilities", "Electric Utilities", country="France", exchange="Euronext", currency="EUR", volume=3_000_000),
    # === Switzerland (SIX) ===
    _ms("NESN.SW", "Nestle SA", "Consumer Defensive", "Packaged Foods", country="Switzerland", exchange="SIX", currency="CHF", volume=3_000_000),
    _ms("ROG.SW", "Roche Holding AG", "Healthcare", "Pharmaceuticals", country="Switzerland", exchange="SIX", currency="CHF", volume=1_000_000),
    _ms("NOVN.SW", "Novartis AG", "Healthcare", "Pharmaceuticals", country="Switzerland", exchange="SIX", currency="CHF", volume=2_000_000),
    _ms("UBSG.SW", "UBS Group AG", "Financial Services", "Banking", country="Switzerland", exchange="SIX", currency="CHF", volume=3_000_000),
    _ms("ABBN.SW", "ABB Ltd.", "Industrials", "Industrial Automation", country="Switzerland", exchange="SIX", currency="CHF", volume=2_000_000),
    # === Canada (TSX) ===
    _ms("RY.TO", "Royal Bank of Canada", "Financial Services", "Banking", country="Canada", exchange="TSX", currency="CAD", volume=5_000_000),
    _ms("TD.TO", "Toronto-Dominion Bank", "Financial Services", "Banking", country="Canada", exchange="TSX", currency="CAD", volume=5_000_000),
    _ms("BNS.TO", "Bank of Nova Scotia", "Financial Services", "Banking", country="Canada", exchange="TSX", currency="CAD", volume=3_000_000),
    _ms("CNQ.TO", "Canadian Natural Resources", "Energy", "Oil & Gas E&P", country="Canada", exchange="TSX", currency="CAD", volume=3_000_000),
    _ms("SU.TO", "Suncor Energy Inc.", "Energy", "Oil & Gas Integrated", country="Canada", exchange="TSX", currency="CAD", volume=5_000_000),
    _ms("SHOP.TO", "Shopify Inc.", "Technology", "E-Commerce", country="Canada", exchange="TSX", currency="CAD", volume=3_000_000),
    _ms("CP.TO", "Canadian Pacific Railway", "Industrials", "Railroads", country="Canada", exchange="TSX", currency="CAD", volume=1_000_000),
    _ms("CNR.TO", "Canadian National Railway", "Industrials", "Railroads", country="Canada", exchange="TSX", currency="CAD", volume=1_000_000),
    # === Nordic / Scandinavia ===
    _ms("NOVO-B.CO", "Novo Nordisk A/S", "Healthcare", "Pharmaceuticals", country="Denmark", exchange="OMX", currency="DKK", volume=2_000_000),
    _ms("EQNR.OL", "Equinor ASA", "Energy", "Oil & Gas Integrated", country="Norway", exchange="OSL", currency="NOK", volume=2_000_000),
    _ms("SEB-A.ST", "Skandinaviska Enskilda", "Financial Services", "Banking", country="Sweden", exchange="OMX", currency="SEK", volume=2_000_000),
    _ms("VOLV-B.ST", "Volvo AB", "Industrials", "Trucking", country="Sweden", exchange="OMX", currency="SEK", volume=2_000_000),
    # === AI / Emerging Tech ===
    _ms("AI", "C3.ai Inc.", "Technology", "AI Software", volume=5_000_000),
    _ms("UPST", "Upstart Holdings Inc.", "Financial Services", "AI Lending", volume=5_000_000),
    _ms("U", "Unity Software Inc.", "Technology", "Gaming Engine", volume=5_000_000),
    _ms("PATH", "UiPath Inc.", "Technology", "AI Software", volume=5_000_000),
    _ms("SOUN", "SoundHound AI Inc.", "Technology", "AI Voice", volume=10_000_000, liquid=False),
    _ms("BBAI", "BigBear.ai Holdings", "Technology", "AI Software", volume=5_000_000, liquid=False),
    _ms("CYN", "Cyanotech Corp.", "Technology", "AI Software", volume=100_000, liquid=False),
    # === Pairs Trading Specific: High-Liquidity Sector Concentrations ===
    # Tech Pairs
    _ms("MSFT", "Microsoft Corp.", "Technology", "Software", volume=30_000_000),
    _ms("ORCL", "Oracle Corp.", "Technology", "Software", volume=10_000_000),
    # Semis Pairs
    _ms("NVDA", "NVIDIA Corp.", "Technology", "Semiconductors", volume=60_000_000),
    _ms("AMD", "Advanced Micro Devices", "Technology", "Semiconductors", volume=40_000_000),
    _ms("INTC", "Intel Corp.", "Technology", "Semiconductors", volume=30_000_000),
    # Beverage Pairs
    _ms("KO", "Coca-Cola Co.", "Consumer Defensive", "Beverages", volume=15_000_000),
    _ms("PEP", "PepsiCo Inc.", "Consumer Defensive", "Beverages & Snacks", volume=6_000_000),
    _ms("KDP", "Keurig Dr Pepper Inc.", "Consumer Defensive", "Beverages", volume=8_000_000),
    # Energy Pairs
    _ms("XOM", "Exxon Mobil Corp.", "Energy", "Oil & Gas Integrated", volume=20_000_000),
    _ms("CVX", "Chevron Corp.", "Energy", "Oil & Gas Integrated", volume=10_000_000),
    _ms("COP", "ConocoPhillips", "Energy", "Oil & Gas E&P", volume=5_000_000),
    # Bank Pairs
    _ms("JPM", "JPMorgan Chase & Co.", "Financial Services", "Banking", volume=10_000_000),
    _ms("BAC", "Bank of America Corp.", "Financial Services", "Banking", volume=25_000_000),
    _ms("WFC", "Wells Fargo & Co.", "Financial Services", "Banking", volume=15_000_000),
    _ms("C", "Citigroup Inc.", "Financial Services", "Banking", volume=12_000_000),
    # Insurance Pairs
    _ms("MET", "MetLife Inc.", "Financial Services", "Insurance", volume=4_000_000),
    _ms("PRU", "Prudential Financial", "Financial Services", "Insurance", volume=2_000_000),
    _ms("AFL", "Aflac Inc.", "Financial Services", "Insurance", volume=2_000_000),
    _ms("ALL", "Allstate Corp.", "Financial Services", "Insurance", volume=1_500_000),
    # Airline Pairs
    _ms("DAL", "Delta Air Lines Inc.", "Industrials", "Airlines", volume=8_000_000),
    _ms("UAL", "United Airlines Holdings", "Industrials", "Airlines", volume=5_000_000),
    _ms("AAL", "American Airlines Group", "Industrials", "Airlines", volume=20_000_000),
    _ms("LUV", "Southwest Airlines Co.", "Industrials", "Airlines", volume=6_000_000),
    # Pharma Pairs
    _ms("PFE", "Pfizer Inc.", "Healthcare", "Pharmaceuticals", volume=20_000_000),
    _ms("MRK", "Merck & Co.", "Healthcare", "Pharmaceuticals", volume=10_000_000),
    _ms("ABBV", "AbbVie Inc.", "Healthcare", "Pharmaceuticals", volume=7_000_000),
    _ms("BMY", "Bristol-Myers Squibb", "Healthcare", "Pharmaceuticals", volume=10_000_000),
    # Retail Pairs
    _ms("HD", "Home Depot Inc.", "Consumer Cyclical", "Home Improvement", volume=5_000_000),
    _ms("LOW", "Lowe's Companies", "Consumer Cyclical", "Home Improvement", volume=3_000_000),
    _ms("TGT", "Target Corp.", "Consumer Cyclical", "Big Box Retail", volume=5_000_000),
    _ms("WMT", "Walmart Inc.", "Consumer Defensive", "Big Box Retail", volume=8_000_000),
    _ms("COST", "Costco Wholesale Corp.", "Consumer Defensive", "Warehouse Retail", volume=3_000_000),
    # Streaming Pairs
    _ms("NFLX", "Netflix Inc.", "Communication Services", "Entertainment", volume=5_000_000),
    _ms("DIS", "Walt Disney Co.", "Communication Services", "Entertainment", volume=10_000_000),
]

# Deduplicate by ticker while preserving order
_seen: set[str] = set()
DEFAULT_UNIVERSE: list[StockMetadata] = []
for s in DEFAULT_UNIVERSE_STOCKS:
    key = s.ticker.upper()
    if key not in _seen:
        _seen.add(key)
        DEFAULT_UNIVERSE.append(s)


class UniverseBuilder:
    def __init__(self, universe_path: str | Path = "data/stock_universe.json") -> None:
        self.universe_path = Path(universe_path)

    def build_default(self) -> StockUniverse:
        return StockUniverse(
            stocks=list(DEFAULT_UNIVERSE),
            name="default",
            description="Curated global stock universe (~400 stocks) across sectors, countries, and exchanges for pairs trading research.",
        )

    def load_or_build(self) -> StockUniverse:
        if self.universe_path.exists():
            return StockUniverse.load(self.universe_path)
        universe = self.build_default()
        universe.save(self.universe_path)
        return universe

    def enrich_from_yahoo(self, universe: StockUniverse | None = None) -> StockUniverse:
        try:
            import yfinance as yf
        except ImportError:
            return (universe or self.load_or_build())

        base = universe or self.load_or_build()
        enriched: list[StockMetadata] = []

        batch_size = 100
        for i in range(0, len(base.stocks), batch_size):
            batch_stocks = base.stocks[i : i + batch_size]
            batch = [s.ticker for s in batch_stocks]
            try:
                ticker_group = yf.Tickers(" ".join(batch))
                info_dict: dict[str, dict[str, Any]] = {}
                for symbol, ticker_obj in getattr(ticker_group, "tickers", {}).items():
                    try:
                        row = ticker_obj.info
                    except Exception:
                        continue
                    if isinstance(row, dict):
                        info_dict[str(symbol).upper()] = row
            except Exception:
                info_dict = {}

            for s in batch_stocks:
                row = info_dict.get(s.ticker.upper())
                if row:
                    sector = str(row.get("sector") or s.sector)
                    industry = str(row.get("industry") or s.industry)
                    country = str(row.get("country") or s.country)
                    exchange = str(row.get("exchange") or s.exchange)
                    currency = str(row.get("currency") or s.currency)
                    mcap = row.get("marketCap") or 0
                    if isinstance(mcap, (int, float)):
                        cap = "mega" if mcap > 200e9 else "large" if mcap > 10e9 else "mid" if mcap > 2e9 else "small"
                    else:
                        cap = s.market_cap_category
                    avg_vol = int(row.get("averageVolume", s.avg_volume) or s.avg_volume)
                    liquid = avg_vol > 100_000 or s.is_liquid
                    name = str(row.get("longName") or row.get("shortName") or s.company_name)
                    enriched.append(
                        StockMetadata(
                            ticker=s.ticker,
                            company_name=name,
                            sector=sector,
                            industry=industry,
                            country=country,
                            exchange=exchange,
                            currency=currency,
                            market_cap_category=cap,
                            avg_volume=avg_vol,
                            is_liquid=liquid,
                        )
                    )
                else:
                    enriched.append(s)

        result = StockUniverse(stocks=enriched, name=base.name, description=base.description)
        result.save(self.universe_path)
        return result
