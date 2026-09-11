"""自有 Hugging Face 镜像探测测试（从主项目迁移）。"""

from __future__ import annotations

from .. import hf_mirror


def _env_reader(values: dict[str, str]):
    return lambda name, default=None: values.get(name, default)


def test_configured_candidates_precede_regional_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        hf_mirror,
        "get_env_var",
        _env_reader(
            {
                "HF_ENDPOINT": "https://custom.example/",
                "AGENT_MATCHBOX_HF_ENDPOINTS": (
                    "https://backup.example; https://custom.example\ninvalid"
                ),
            }
        ),
    )
    monkeypatch.setattr(hf_mirror, "is_mainland_china", lambda: False)

    assert hf_mirror.get_hf_candidates(probe=False) == [
        "https://custom.example",
        "https://backup.example",
        hf_mirror.OFFICIAL_HF_ENDPOINT,
        hf_mirror.MAINLAND_HF_MIRROR,
    ]


def test_mainland_region_prefers_mirror(monkeypatch) -> None:
    monkeypatch.setattr(hf_mirror, "get_env_var", _env_reader({}))
    monkeypatch.setattr(hf_mirror, "is_mainland_china", lambda: True)

    assert hf_mirror.get_hf_candidates(probe=False) == [
        hf_mirror.MAINLAND_HF_MIRROR,
        hf_mirror.OFFICIAL_HF_ENDPOINT,
    ]


def test_reachable_candidates_are_promoted_and_cached(monkeypatch) -> None:
    monkeypatch.setattr(hf_mirror, "get_env_var", _env_reader({}))
    monkeypatch.setattr(hf_mirror, "is_mainland_china", lambda: False)
    hf_mirror.reset_hf_mirror_cache()
    calls: list[str] = []

    def fake_probe(endpoint: str) -> bool:
        calls.append(endpoint)
        return endpoint == hf_mirror.MAINLAND_HF_MIRROR

    monkeypatch.setattr(hf_mirror, "probe_hf_endpoint", fake_probe)

    expected = [hf_mirror.MAINLAND_HF_MIRROR, hf_mirror.OFFICIAL_HF_ENDPOINT]
    assert hf_mirror.get_hf_candidates() == expected
    assert hf_mirror.get_hf_candidates() == expected
    assert calls == [hf_mirror.OFFICIAL_HF_ENDPOINT, hf_mirror.MAINLAND_HF_MIRROR]


def test_region_requires_consistent_majority(monkeypatch) -> None:
    monkeypatch.setattr(hf_mirror, "get_env_var", _env_reader({}))
    monkeypatch.setattr(
        hf_mirror,
        "_probe_region_provider",
        lambda url, _timeout: {
            hf_mirror._GEOIP_PROVIDERS[0]: "CN",
            hf_mirror._GEOIP_PROVIDERS[1]: "CN",
            hf_mirror._GEOIP_PROVIDERS[2]: "US",
        }[url],
    )
    hf_mirror.reset_hf_mirror_cache()

    assert hf_mirror.is_mainland_china() is True
