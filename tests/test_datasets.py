from __future__ import annotations

import pytest

from pairs_trading.features.datasets import REGISTRY, get_dataset, list_datasets, load_dataset


class TestDatasetsRegistry:
    def test_known_datasets(self):
        assert "financial_phrasebank" in REGISTRY
        assert "fiqa" in REGISTRY
        assert "nosible" in REGISTRY
        assert "twitter_financial" in REGISTRY

    def test_list_datasets(self):
        specs = list_datasets()
        assert len(specs) == len(REGISTRY)
        names = [s.name for s in specs]
        assert "Financial PhraseBank (50% agree)" in names

    def test_get_dataset_known(self):
        spec = get_dataset("fiqa")
        assert spec.name == "FiQA-SA"
        assert spec.source == "huggingface"

    def test_get_dataset_unknown(self):
        with pytest.raises(KeyError, match="Unknown dataset"):
            get_dataset("nonexistent")

    def test_financial_phrasebank_spec(self):
        spec = get_dataset("financial_phrasebank")
        assert spec.source == "url"
        assert spec.path == "takala/financial_phrasebank"
        assert spec.max_samples_default == 500

    def test_nosible_spec(self):
        spec = get_dataset("nosible")
        assert spec.source == "huggingface"
        assert spec.path == "NOSIBLE/financial-sentiment"
        assert spec.label_col == "label"
        assert spec.max_samples_default == 1000


@pytest.mark.skip(reason="Needs network for huggingface_hub download")
class TestDatasetsLoadNetwork:
    def test_load_financial_phrasebank(self):
        df = load_dataset("financial_phrasebank", max_samples=50)
        assert len(df) == 50
        assert "text" in df.columns
        assert "label" in df.columns
        assert set(df["label"].unique()) <= {"positive", "negative", "neutral"}

    @pytest.mark.skip(reason="Needs network")
    def test_load_fiqa(self):
        df = load_dataset("fiqa", max_samples=20)
        assert len(df) == 20
        assert set(df["label"].unique()) <= {"positive", "negative", "neutral"}

    @pytest.mark.skip(reason="Needs network")
    def test_load_nosible(self):
        df = load_dataset("nosible", max_samples=20)
        assert len(df) == 20
        assert set(df["label"].unique()) <= {"positive", "negative", "neutral"}

    @pytest.mark.skip(reason="Needs network")
    def test_load_twitter_financial(self):
        df = load_dataset("twitter_financial", max_samples=20)
        assert len(df) == 20
        assert set(df["label"].unique()) <= {"positive", "negative", "neutral"}
