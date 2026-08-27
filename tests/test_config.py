"""Tests for configuration loading and validation."""

from config.settings import load_feeds_config, settings


def test_feeds_config_loading():
    config = load_feeds_config()
    assert config is not None
    assert "tech_news" in config.categories
    assert "seo_tips" in config.categories
    assert "gadgets" in config.categories
    assert "monetization" in config.categories

    # Verify feeds list in tech_news
    tech_feeds = config.categories["tech_news"].feeds
    assert len(tech_feeds) > 0
    assert any(f.name == "TechCrunch" for f in tech_feeds)


def test_settings_defaults():
    assert settings.gemini_model is not None
    assert settings.db_path.name == "history.db"
    assert 6 <= settings.digest_story_count <= 8
    assert settings.digest_target_word_count >= 800
