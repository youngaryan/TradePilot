from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_DIR = Path("data/sentiment_eval_cache")

LABEL_MAP_STANDARD = {"positive": "positive", "negative": "negative", "neutral": "neutral"}


@dataclass
class DatasetSpec:
    name: str
    description: str
    source: str
    path: str
    config: str | None
    split: str
    text_col: str
    label_col: str
    label_map: dict[str, str] | None
    max_samples_default: int = 500


REGISTRY: dict[str, DatasetSpec] = {
    "financial_phrasebank": DatasetSpec(
        name="Financial PhraseBank (50% agree)",
        description="~4,846 expert-labeled financial news sentences from OMX Helsinki.",
        source="url",
        path="takala/financial_phrasebank",
        config="sentences_50agree",
        split="train",
        text_col="sentence",
        label_col="label",
        label_map=LABEL_MAP_STANDARD,
        max_samples_default=500,
    ),
    "fiqa": DatasetSpec(
        name="FiQA-SA",
        description="1,173 financial sentiment samples from the FiQA 2018 challenge (SeekingAlpha headlines + microblogs).",
        source="huggingface",
        path="TheFinAI/fiqa-sentiment-classification",
        config=None,
        split="train",
        text_col="sentence",
        label_col="score",
        label_map=None,
        max_samples_default=500,
    ),
    "nosible": DatasetSpec(
        name="NOSIBLE Financial Sentiment",
        description="100,000 LLM-annotated financial news samples (ODC-By license).",
        source="huggingface",
        path="NOSIBLE/financial-sentiment",
        config=None,
        split="train",
        text_col="text",
        label_col="label",
        label_map={"positive": "positive", "negative": "negative", "neutral": "neutral"},
        max_samples_default=1000,
    ),
    "twitter_financial": DatasetSpec(
        name="Twitter Financial News Sentiment",
        description="~12,000 finance-related tweets with sentiment labels (MIT license).",
        source="huggingface",
        path="arabianpost/twitter-financial-news-sentiment",
        config=None,
        split="train",
        text_col="text",
        label_col="label",
        label_map={"0": "negative", "1": "neutral", "2": "positive"},
        max_samples_default=500,
    ),
}


def list_datasets() -> list[DatasetSpec]:
    return list(REGISTRY.values())


def get_dataset(name: str) -> DatasetSpec:
    spec = REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"Unknown dataset '{name}'. Available: {sorted(REGISTRY)}")
    return spec


def _load_financial_phrasebank(spec: DatasetSpec, max_samples: int | None) -> pd.DataFrame:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _CACHE_DIR / "financial_phrasebank_50agree.csv"
    if cache_path.exists():
        df = pd.read_csv(cache_path)
        df[spec.label_col] = df[spec.label_col].str.lower()
        if max_samples is not None and max_samples < len(df):
            df = df.sample(n=max_samples, random_state=42).reset_index(drop=True)
        return df

    from huggingface_hub import hf_hub_download

    zip_path = hf_hub_download(repo_id=spec.path, filename="data/FinancialPhraseBank-v1.0.zip", repo_type="dataset")
    records: list[dict[str, str]] = []
    lm = spec.label_map or LABEL_MAP_STANDARD
    with zipfile.ZipFile(zip_path, "r") as zf:
        target = "FinancialPhraseBank-v1.0/Sentences_50Agree.txt"
        with zf.open(target) as f:
            for line_bytes in f:
                line = line_bytes.decode("latin-1").strip()
                if not line or "@" not in line:
                    continue
                sentence, raw_label = line.rsplit("@", 1)
                label = raw_label.strip().lower()
                normalized = lm.get(label)
                if normalized is None:
                    continue
                records.append({"text": sentence.strip(), "label": normalized})

    df = pd.DataFrame(records)
    df.to_csv(cache_path, index=False)
    if max_samples is not None and max_samples < len(df):
        df = df.sample(n=max_samples, random_state=42).reset_index(drop=True)
    return df


def _load_huggingface(spec: DatasetSpec, max_samples: int | None) -> pd.DataFrame:
    from huggingface_hub import hf_hub_download, list_repo_files

    all_files = list_repo_files(spec.path, repo_type="dataset")
    data_files = [f for f in all_files if any(f.endswith(ext) for ext in (".parquet", ".csv")) and not f.endswith(".gitattributes")]

    split_lower = spec.split.lower()
    candidates = [f for f in data_files if split_lower in Path(f).name.lower()]
    if not candidates:
        candidates = [f for f in data_files if split_lower in f.lower()]
    if not candidates:
        candidates = data_files
    if not candidates:
        raise FileNotFoundError(f"No data files found in {spec.path}.")

    dfs: list[pd.DataFrame] = []
    for file in candidates:
        local_path = hf_hub_download(repo_id=spec.path, filename=file, repo_type="dataset")
        if file.endswith(".parquet"):
            chunk = pd.read_parquet(local_path)
        else:
            chunk = pd.read_csv(local_path)
        dfs.append(chunk)

    df = pd.concat(dfs, ignore_index=True)
    if spec.text_col not in df.columns or spec.label_col not in df.columns:
        raise KeyError(
            f"Expected columns '{spec.text_col}' and '{spec.label_col}' not found in {spec.path}. "
            f"Available columns: {list(df.columns)}"
        )

    df = df[[spec.text_col, spec.label_col]].copy()
    df.columns = ["text", "label"]

    if pd.api.types.is_numeric_dtype(df["label"]):
        def _numeric_label(v: float) -> str:
            if v > 0:
                return "positive"
            elif v < 0:
                return "negative"
            return "neutral"

        df["label"] = df["label"].astype(float).apply(_numeric_label)
    else:
        lm = spec.label_map or LABEL_MAP_STANDARD

        def _apply_label(raw: str) -> str:
            raw = raw.lower().strip()
            return lm.get(raw, raw)

        df["label"] = df["label"].astype(str).apply(_apply_label)

    if max_samples is not None and max_samples < len(df):
        df = df.sample(n=max_samples, random_state=42).reset_index(drop=True)

    return df


def load_dataset(name: str, max_samples: int | None = None) -> pd.DataFrame:
    spec = get_dataset(name)
    if spec.source == "url":
        return _load_financial_phrasebank(spec, max_samples)
    elif spec.source == "huggingface":
        return _load_huggingface(spec, max_samples)
    else:
        raise ValueError(f"Unknown source type: {spec.source}")
