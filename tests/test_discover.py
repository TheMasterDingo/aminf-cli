from aminf.cli import build_parser
from aminf.discover import open_mice
from aminf.models import MODELS
from tests.fakes import EmptyBackend, FakeBackend, R2_DONGLE_IFACES, info


def test_r2_dongle_is_found_on_ff13():
    mice, errors = open_mice(FakeBackend(R2_DONGLE_IFACES), dry_run=True)
    assert [m.model.name for m in mice] == [".100R2"] and errors == []
    assert mice[0].connection == "dongle"


def test_r2_cable_is_found():
    mice, _ = open_mice(FakeBackend([info(0x0E8D, 0x0881, 0xFF13, 1)]), dry_run=True)
    assert [(m.model.name, m.connection) for m in mice] == [(".100R2", "usb")]


def test_mouse_flag_accepts_100r2():
    assert build_parser().parse_args(["--mouse", "100r2", "status"]).mouse == "100r2"


def test_named_mouse_missing_reports_it():
    mice, errors = open_mice(EmptyBackend(), which="100r2")
    assert mice == [] and errors == ["AM Infinity .100R2 not found."]


def test_boot_pids_are_ignored():   # 3151:402B and 3151:402E must never be opened
    assert open_mice(FakeBackend([info(0x3151, 0x402B, 0xFF01)]))[0] == []
    assert open_mice(FakeBackend([info(0x3151, 0x402E, 0xFF01)]))[0] == []


def test_dry_run_with_nothing_connected_defaults_to_the_97():   # 2.2.0 behaviour
    mice, _ = open_mice(EmptyBackend(), dry_run=True)
    assert [m.model.name for m in mice] == [".97"]


def test_model_table():
    assert list(MODELS) == ["97", "100r2", "100"]
    assert MODELS["100r2"].buttons[-1] == "bottom" and len(MODELS["97"].buttons) == 7
    assert (MODELS["97"].lod_max, MODELS["100r2"].lod_max) == (2, 4)
    # the configurator's code says 30000 for the .97 and .100R2, but both real
    # mice answer status 2 to anything above 26000
    assert [m.dpi_max for m in MODELS.values()] == [26000, 26000, 26000]
