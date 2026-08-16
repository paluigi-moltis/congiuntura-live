"""Tests for the LLMExtraction model loading and introspection."""

from __future__ import annotations

import pytest

from congiuntura_live.settings import load_extraction_model


@pytest.fixture
def model_class():
    return load_extraction_model("config/extraction_model.py")


class TestModelLoading:
    def test_model_loads_from_python_file(self, model_class):
        assert model_class.__name__ == "LLMExtraction"

    def test_model_is_pydantic(self, model_class):
        from pydantic import BaseModel

        assert issubclass(model_class, BaseModel)

    def test_model_has_expected_fields(self, model_class):
        fields = set(model_class.model_fields.keys())
        assert fields == {"topic", "country", "sentiment", "title_en", "summary_en", "key_figures"}

    def test_no_auto_fields_in_model(self, model_class):
        """The LLM must NOT see url/date/publisher fields."""
        fields = set(model_class.model_fields.keys())
        forbidden = {"url", "url_hash", "published", "fetched_at", "publisher", "processing_model"}
        assert not (fields & forbidden), "Auto fields leaked into LLM model"

    def test_topic_has_import_export_prices(self, model_class):
        import typing

        topic_ann = model_class.model_fields["topic"].annotation
        choices = typing.get_args(topic_ann)
        assert "Import prices" in choices
        assert "Export prices" in choices

    def test_topic_has_expected_categories(self, model_class):
        import typing

        topic_ann = model_class.model_fields["topic"].annotation
        choices = typing.get_args(topic_ann)
        expected = {"Consumer prices", "Producer prices", "GDP", "Industrial production"}
        assert expected.issubset(set(choices))

    def test_country_choices(self, model_class):
        import typing

        country_ann = model_class.model_fields["country"].annotation
        choices = typing.get_args(country_ann)
        assert "Italy" in choices
        assert "Euro area" in choices
        assert "Germany" in choices

    def test_sentiment_choices(self, model_class):
        import typing

        sentiment_ann = model_class.model_fields["sentiment"].annotation
        choices = typing.get_args(sentiment_ann)
        assert set(choices) == {"positive", "negative", "neutral"}

    def test_can_instantiate_with_valid_data(self, model_class):
        instance = model_class(
            topic="GDP",
            country="Italy",
            sentiment="neutral",
            title_en="GDP grew by 0.3% in Q1 2025",
            summary_en="GDP rose by 0.3% in Q1 2025.",
            key_figures="+0.3% QoQ, +0.9% YoY",
        )
        assert instance.topic == "GDP"
        assert instance.sentiment == "neutral"

    def test_rejects_invalid_topic(self, model_class):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            model_class(
                topic="Invalid topic",
                country="Italy",
                sentiment="neutral",
                summary_en="test",
                key_figures="test",
            )
