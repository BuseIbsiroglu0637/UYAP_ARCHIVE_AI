#!/usr/bin/env python3
"""The AI provider defaults to the local model; the cloud needs an explicit setting.

Settings are faked, so neither the real environment nor the Windows registry is
read and no request leaves the test.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_provider


def settings(values):
    """Stand-in for _read_user_setting: empty or missing means "use the default"."""
    def read(name, default=""):
        value = str(values.get(name) or "").strip()
        if value:
            return value, "process_environment"
        return str(default or "").strip(), None
    return read


def config_with(values):
    with patch.object(ai_provider, "_read_user_setting", side_effect=settings(values)):
        return ai_provider._provider_config()


def test_no_setting_means_local_even_with_an_openai_key():
    config = config_with({"OPENAI_API_KEY": "sk-test"})
    assert config["provider"] == "lmstudio", config
    assert config["localOnly"] is True and config["requiresApiKey"] is False
    assert config["providerSource"] == "default"
    assert config["endpoint"] == ai_provider.LMSTUDIO_RESPONSES_URL


def test_empty_setting_is_not_the_cloud():
    config = config_with({"UYAP_AI_PROVIDER": "  ", "OPENAI_API_KEY": "sk-test"})
    assert config["provider"] == "lmstudio", config


def test_cloud_only_when_chosen_explicitly():
    config = config_with({"UYAP_AI_PROVIDER": "OpenAI", "OPENAI_API_KEY": "sk-test"})
    assert config["provider"] == "openai" and config["localOnly"] is False
    assert config["providerSource"] == "process_environment"


def test_unknown_value_is_refused_not_sent_anywhere():
    config = config_with({"UYAP_AI_PROVIDER": "azure"})
    assert config.get("unsupported") is True and config["endpoint"] == ""


if __name__ == "__main__":
    test_no_setting_means_local_even_with_an_openai_key()
    test_empty_setting_is_not_the_cloud()
    test_cloud_only_when_chosen_explicitly()
    test_unknown_value_is_refused_not_sent_anywhere()
    print("AI provider default is local; cloud only on explicit UYAP_AI_PROVIDER=openai: PASS")
