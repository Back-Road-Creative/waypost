"""The inputs directory: where a deployment's private files (brand.yaml, ...) live."""

import pytest

from seo_manager import cli, inputs, paths

BRAND = """\
seo:
  cadence:
    timezone: America/New_York
    lead_hours: 72
    ready_depth: 2
    superlong_day: sat
    regions:
      north:
        dow: [sun]
        time: "14:00"
        shorts_times: ["18:00"]
"""


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for var in (
        "SEO_INPUTS_DIR",
        "SEO_BRAND_YAML",
        "SEO_SCHEDULE_DB",
        "SEO_DECISIONS_DB",
        "SEO_MANAGER_DATA_HOME",
        "XDG_DATA_HOME",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))  # ~ on Windows
    return tmp_path


def test_default_is_under_the_data_home(clean_env):
    assert inputs.inputs_dir() == paths.data_home() / "inputs"


def test_env_var_wins_over_the_default(clean_env, monkeypatch):
    monkeypatch.setenv("SEO_INPUTS_DIR", str(clean_env / "mine"))
    assert inputs.inputs_dir() == clean_env / "mine"


def test_env_var_expands_a_home_prefix(clean_env, monkeypatch):
    monkeypatch.setenv("SEO_INPUTS_DIR", "~/private")
    assert inputs.inputs_dir() == clean_env / "home" / "private"


def test_empty_env_var_falls_back_to_the_default(clean_env, monkeypatch):
    monkeypatch.setenv("SEO_INPUTS_DIR", "")
    assert inputs.inputs_dir() == paths.data_home() / "inputs"


def test_missing_dir_fails_naming_the_variable_and_the_path(clean_env, monkeypatch):
    gone = clean_env / "gone"
    monkeypatch.setenv("SEO_INPUTS_DIR", str(gone))
    with pytest.raises(inputs.InputsDirError) as exc:
        inputs.require_inputs_dir()
    assert "SEO_INPUTS_DIR" in str(exc.value)
    assert str(gone) in str(exc.value)


def test_existing_dir_is_returned(clean_env, monkeypatch):
    monkeypatch.setenv("SEO_INPUTS_DIR", str(clean_env))
    assert inputs.require_inputs_dir() == clean_env


def test_a_file_in_place_of_the_dir_is_refused(clean_env, monkeypatch):
    f = clean_env / "afile"
    f.write_text("x")
    monkeypatch.setenv("SEO_INPUTS_DIR", str(f))
    with pytest.raises(inputs.InputsDirError, match="not a directory"):
        inputs.require_inputs_dir()


def test_input_path_joins_under_the_dir(clean_env, monkeypatch):
    (clean_env / "config").mkdir()
    (clean_env / "config" / "sites.yaml").write_text("x")
    monkeypatch.setenv("SEO_INPUTS_DIR", str(clean_env))
    assert inputs.input_path("config", "sites.yaml") == (
        clean_env / "config" / "sites.yaml"
    )


def test_missing_input_file_fails_naming_it(clean_env, monkeypatch):
    monkeypatch.setenv("SEO_INPUTS_DIR", str(clean_env))
    with pytest.raises(inputs.InputsDirError, match=r"brand\.yaml"):
        inputs.input_path("brand.yaml")


def test_input_path_refuses_to_leave_the_dir(clean_env, monkeypatch):
    monkeypatch.setenv("SEO_INPUTS_DIR", str(clean_env))
    with pytest.raises(inputs.InputsDirError, match="outside"):
        inputs.input_path("..", "elsewhere.yaml")


# --- brand.yaml precedence: flag > SEO_BRAND_YAML > $SEO_INPUTS_DIR/brand.yaml ---


def test_brand_yaml_unset_everywhere_stays_unconfigured(clean_env):
    assert inputs.default_brand_yaml() is None


def test_brand_yaml_env_beats_the_inputs_dir(clean_env, monkeypatch):
    (clean_env / "brand.yaml").write_text(BRAND)
    monkeypatch.setenv("SEO_INPUTS_DIR", str(clean_env))
    monkeypatch.setenv("SEO_BRAND_YAML", str(clean_env / "other.yaml"))
    assert inputs.default_brand_yaml() == str(clean_env / "other.yaml")


def test_brand_yaml_comes_from_the_inputs_dir_when_set(clean_env, monkeypatch):
    (clean_env / "brand.yaml").write_text(BRAND)
    monkeypatch.setenv("SEO_INPUTS_DIR", str(clean_env))
    assert inputs.default_brand_yaml() == str(clean_env / "brand.yaml")


def test_schedule_uses_brand_yaml_from_the_inputs_dir(clean_env, monkeypatch, capsys):
    (clean_env / "brand.yaml").write_text(BRAND)
    monkeypatch.setenv("SEO_INPUTS_DIR", str(clean_env))
    db = clean_env / "s.sqlite3"
    rc = cli.main(
        ["schedule", "--db", str(db), "--init", "ready-depth", "--region", "north"]
    )
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert '"region": "north"' in cap.out


def test_schedule_with_a_missing_inputs_dir_exits_2_with_the_reason(
    clean_env, monkeypatch, capsys
):
    monkeypatch.setenv("SEO_INPUTS_DIR", str(clean_env / "gone"))
    rc = cli.main(
        ["schedule", "--db", str(clean_env / "s.db"), "--init", "ready-depth",
         "--region", "north"]
    )  # fmt: skip
    err = capsys.readouterr().err
    assert rc == 2
    assert "SEO_INPUTS_DIR" in err


def test_schedule_without_any_brand_yaml_still_says_so(clean_env, capsys):
    rc = cli.main(
        ["schedule", "--db", str(clean_env / "s.db"), "--init", "ready-depth",
         "--region", "north"]
    )  # fmt: skip
    assert rc == 2
    assert "--brand-yaml" in capsys.readouterr().err
