import pytest

from winnower import __version__
from winnower.config import (
    API_KEY_ENV,
    ConfigError,
    SearchOptions,
    Settings,
    find_api_key,
    load_settings,
    parse_dotenv,
    parse_size,
)


def test_package_has_a_version():
    assert __version__


class TestApiKey:
    def test_comes_from_the_environment(self, tmp_path):
        assert find_api_key({API_KEY_ENV: " abc123 "}, tmp_path / ".env") == "abc123"

    def test_comes_from_dotenv_when_the_environment_has_none(self, tmp_path):
        dotenv = tmp_path / ".env"
        dotenv.write_text(f"{API_KEY_ENV}=fromfile\n", encoding="utf-8")
        assert find_api_key({}, dotenv) == "fromfile"

    def test_environment_beats_dotenv(self, tmp_path):
        dotenv = tmp_path / ".env"
        dotenv.write_text(f"{API_KEY_ENV}=fromfile\n", encoding="utf-8")
        assert find_api_key({API_KEY_ENV: "fromenv"}, dotenv) == "fromenv"

    def test_a_windows_bom_does_not_hide_the_variable(self, tmp_path):
        dotenv = tmp_path / ".env"
        dotenv.write_bytes(b"\xef\xbb\xbf" + f"{API_KEY_ENV}=bomkey\n".encode())
        assert find_api_key({}, dotenv) == "bomkey"

    def test_missing_says_where_to_get_one(self, tmp_path):
        with pytest.raises(ConfigError, match="pixabay.com/api/docs"):
            find_api_key({}, tmp_path / ".env")

    def test_the_example_placeholder_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="placeholder"):
            find_api_key({API_KEY_ENV: "your_key_here"}, tmp_path / ".env")

    def test_never_appears_in_a_repr(self):
        assert "SECRET" not in repr(Settings(api_key="SECRET"))

    def test_cannot_be_passed_in_as_an_override(self, tmp_path):
        with pytest.raises(ConfigError, match="never passed in"):
            load_settings({"api_key": "x"}, env={API_KEY_ENV: "k"}, dotenv_path=tmp_path / ".env")


class TestDotenv:
    def test_the_syntax_people_write(self):
        text = """
        # a comment
        A=1
        export B = two
        C="quoted # not a comment"
        D='single'
        E=plain # trailing comment
        EMPTY=
        no equals sign here
        """
        assert parse_dotenv(text) == {
            "A": "1",
            "B": "two",
            "C": "quoted # not a comment",
            "D": "single",
            "E": "plain",
            "EMPTY": "",
        }


class TestLoadSettings:
    def env(self):
        return {API_KEY_ENV: "k"}

    def test_defaults(self, tmp_path):
        s = load_settings(env=self.env(), dotenv_path=tmp_path / ".env")
        assert (s.api_key, s.candidates, s.size, s.resize_mode, s.output_format) == (
            "k", 5, (512, 512), "crop", "png",
        )  # fmt: skip

    def test_overrides_win_and_none_means_not_given(self, tmp_path):
        s = load_settings(
            {"candidates": 8, "size": (64, 32), "resize_mode": None},
            env=self.env(),
            dotenv_path=tmp_path / ".env",
        )
        assert (s.candidates, s.size, s.resize_mode) == (8, (64, 32), "crop")

    def test_no_key_is_an_error_before_anything_else_happens(self, tmp_path):
        with pytest.raises(ConfigError):
            load_settings(env={}, dotenv_path=tmp_path / ".env")


class TestValidation:
    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"candidates": 0}, "candidates"),
            ({"candidates": 201}, "candidates"),
            ({"term_template": "icon"}, "{term}"),
            ({"size": (0, 10)}, "size"),
            ({"size": (10, 99999)}, "size"),
            ({"resize_mode": "stretch"}, "resize mode"),
            ({"output_format": "gif"}, "output format"),
            ({"background": "red"}, "#rrggbb"),
        ],
    )
    def test_settings_rejects(self, kwargs, fragment):
        with pytest.raises(ConfigError, match=fragment):
            Settings(**kwargs)

    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"image_type": "gif"}, "image_type"),
            ({"orientation": "diagonal"}, "orientation"),
            ({"category": "cats"}, "category"),
            ({"colors": ("red", "mauve")}, "colors"),
            ({"lang": "xx"}, "lang"),
            ({"order": "random"}, "order"),
            ({"min_width": -1}, "negative"),
        ],
    )
    def test_search_options_rejects(self, kwargs, fragment):
        with pytest.raises(ConfigError, match=fragment):
            SearchOptions(**kwargs)

    def test_accepts_the_boundaries(self):
        Settings(candidates=1, size=(1, 1), background="#FFaa00")
        Settings(candidates=200, size=(8192, 8192))

    def test_search_term_template(self):
        assert Settings(term_template="{term} icon").search_term("hot dog") == "hot dog icon"
        assert Settings().search_term("hot dog") == "hot dog"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("512x512", (512, 512)),
        ("640X480", (640, 480)),
        ("256", (256, 256)),
        (" 64 x 32 ", (64, 32)),
    ],
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "x", "512x", "-5x5", "big"])
def test_parse_size_rejects(text):
    with pytest.raises(ConfigError):
        parse_size(text)
