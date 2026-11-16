import pytest

from character_migrator.db import EnvConfigError, ensure_distinct, parse_environments


def profile(key, name, host="db.example", **extra):
    prefix = f"ENV_{key.upper()}"
    env = {
        f"{prefix}_HOST": host,
        f"{prefix}_USER": "user",
        f"{prefix}_PASSWORD": "secret",
        f"{prefix}_NAME": name,
    }
    env.update({f"{prefix}_{k}": v for k, v in extra.items()})
    return env


def test_named_profiles_are_loaded_in_order():
    env = {
        "MIGRATOR_ENVS": "s1_creative, S1_Live",
        **profile("s1_creative", "creative"),
        **profile("s1_live", "live", PORT="3307", LABEL="S1 Live", PROTECTED="1"),
    }
    registry = parse_environments(env)
    assert list(registry.environments) == ["s1_creative", "s1_live"]
    live = registry.get("S1_LIVE")
    assert live.label == "S1 Live"
    assert live.protected is True
    assert live.db.port == 3307
    assert registry.get("s1_creative").protected is False
    assert registry.get("s1_creative").label == "s1_creative"


def test_password_is_not_in_repr():
    registry = parse_environments(
        {"MIGRATOR_ENVS": "a,b", **profile("a", "db_a"), **profile("b", "db_b")}
    )
    assert "secret" not in repr(registry.get("a"))


def test_missing_variables_are_reported():
    env = {"MIGRATOR_ENVS": "a,b", **profile("a", "db_a"), "ENV_B_HOST": "x"}
    with pytest.raises(EnvConfigError, match="ENV_B_USER, ENV_B_PASSWORD, ENV_B_NAME"):
        parse_environments(env)


@pytest.mark.parametrize("keys", ["a,a", "a,b-c", "a"])
def test_bad_environment_lists_are_rejected(keys):
    env = {"MIGRATOR_ENVS": keys, **profile("a", "db_a"), **profile("b", "db_b")}
    with pytest.raises(EnvConfigError):
        parse_environments(env)


def test_default_with_a_typo_fails_fast():
    env = {
        "MIGRATOR_ENVS": "a,b",
        "MIGRATOR_DEFAULT_TO": "c",
        **profile("a", "db_a"),
        **profile("b", "db_b"),
    }
    with pytest.raises(EnvConfigError, match="Unknown environment: c"):
        parse_environments(env)


def test_legacy_layout_maps_to_creative_and_protected_live():
    env = {}
    for prefix, name in (("CREATIVE_DB", "creative"), ("LIVE_DB", "live")):
        env |= {
            f"{prefix}_HOST": "h",
            f"{prefix}_USER": "u",
            f"{prefix}_PASSWORD": "p",
            f"{prefix}_NAME": name,
        }
    registry = parse_environments(env)
    assert (registry.default_source, registry.default_target) == ("creative", "live")
    assert registry.get("live").protected is True


def test_no_environments_is_an_error():
    with pytest.raises(EnvConfigError, match="MIGRATOR_ENVS"):
        parse_environments({})


def test_same_database_under_two_names_is_blocked():
    env = {
        "MIGRATOR_ENVS": "a,b",
        **profile("a", "Live", host="DB.example"),
        **profile("b", "live", host="db.example"),
    }
    registry = parse_environments(env)
    with pytest.raises(EnvConfigError, match="same database"):
        ensure_distinct(registry.get("a"), registry.get("b"))
