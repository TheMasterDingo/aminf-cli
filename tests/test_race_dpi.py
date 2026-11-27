"""DPI on the .97 / .100R2, against the simulated .100R2."""
import pytest

from aminf.errors import AmError, NotConfirmed


def test_read_dpi(r2):
    d = r2[0].read("dpi")
    assert d["count"] == 8 and d["active"] == 3 and d["button"] is True
    assert [s["x"] for s in d["stages"]] == [400, 800, 1200, 1600, 3200, 6400, 10000, 30000]
    assert [s["y"] for s in d["stages"]] == [400, 800, 1200, 1600, 3200, 6400, 10000, 30000]
    assert [s["colour"] for s in d["stages"]] == [
        "#FFFFFF", "#FF8000", "#FFFF00", "#00FF00", "#00FFFF", "#0000FF", "#8B00FF", "#FFFFFF"]


def test_set_stage_value(r2):
    assert r2[0].write("dpi", {"stages": {7: {"x": 26000}}}) == {"stages": {7: {"x": 26000, "y": 26000}}}
    assert r2[1].sent_data(12484) == bytes([7]) + (26000).to_bytes(2, "little") * 2
    assert 12487 not in r2[1].sent_cmds()         # stage 8 is not the active one


def test_separate_y(r2):                      # x alone sets both axes; y is independent when given
    r2[0].write("dpi", {"stages": {7: {"x": 2000, "y": 1000}}})
    s = r2[0].read("dpi")["stages"][7]
    assert (s["x"], s["y"]) == (2000, 1000)


def test_y_alone_leaves_x(r2):
    r2[0].write("dpi", {"stages": {7: {"y": 1000}}})
    s = r2[0].read("dpi")["stages"][7]
    assert (s["x"], s["y"]) == (30000, 1000)


def test_changing_the_active_stage_reselects_it(r2):
    r2[0].write("dpi", {"stages": {3: {"x": 1800}}})
    assert [c for c in r2[1].sent_cmds() if c in (12484, 12487)] == [12484, 12487]
    assert r2[1].sent_data(12487) == bytes([3])


def test_stage_count_then_active(r2):
    assert r2[0].write("dpi", {"count": 4, "active": 1}) == {"count": 4, "active": 1}
    assert [c for c in r2[1].sent_cmds() if c in (12486, 12487)] == [12486, 12487]
    assert r2[1].sent_data(12486) == bytes([4]) and r2[1].sent_data(12487) == bytes([1])


def test_shrinking_the_count_pulls_the_active_stage_in(r2):
    r2[0].write("dpi", {"count": 2})              # active was stage index 3
    assert r2[0].read("dpi")["active"] == 1


def test_active_must_be_inside_count(r2):
    with pytest.raises(AmError):
        r2[0].write("dpi", {"count": 2, "active": 3})
    with pytest.raises(AmError):
        r2[0].write("dpi", {"active": 8})
    with pytest.raises(AmError):
        r2[0].write("dpi", {"count": 9})
    assert not [c for c in r2[1].sent_cmds() if c in (12486, 12487)]


@pytest.mark.parametrize("bad", [0, 49, 1625, 30050, "fast", True])
def test_dpi_value_rules(r2, bad):
    with pytest.raises(AmError):
        r2[0].write("dpi", {"stages": {0: {"x": bad}}})
    assert 12484 not in r2[1].sent_cmds()


def test_stage_index_rules(r2):
    with pytest.raises(AmError):
        r2[0].write("dpi", {"stages": {8: {"x": 800}}})


def test_strict_false_allows_what_the_field_can_hold(r2):
    r2[0].write("dpi", {"stages": {7: {"x": 31000}}}, strict=False)
    assert r2[0].read("dpi")["stages"][7]["x"] == 31000
    with pytest.raises(AmError):
        r2[0].write("dpi", {"stages": {7: {"x": 70000}}}, strict=False)


def test_colour_goes_to_the_pinned_nv(r2):
    assert r2[0].write("dpi", {"stages": {1: {"colour": "#00ff00"}}}) == {"stages": {1: {"colour": "#00FF00"}}}
    assert r2[1].sent_data(2573)[:2] == (25122).to_bytes(2, "little")
    assert r2[0].read("dpi")["stages"][1]["colour"] == "#00FF00"
    assert 12484 not in r2[1].sent_cmds()         # a colour change does not rewrite the DPI value


def test_device_rejection_is_reported(r2):
    r2[1].reject = {12484: 0x05}
    with pytest.raises(AmError, match="rejected"):
        r2[0].write("dpi", {"stages": {7: {"x": 30000, "y": 1000}}})


def test_unconfirmed_stage_write(r2):
    r2[1].drop_writes = {12484}
    with pytest.raises(NotConfirmed):
        r2[0].write("dpi", {"stages": {7: {"x": 1000}}})


def test_dpi_button(r2):
    assert r2[0].write("dpi", {"button": False}) == {"button": False}
    assert r2[0].read("dpi")["button"] is False


def test_unknown_dpi_key(r2):
    with pytest.raises(AmError, match="sideways"):
        r2[0].write("dpi", {"sideways": 1})


# Found on the owner's .100R2: the firmware rejects any DPI above 26000
# (status 2), although its last stage shipped holding 30000.

def test_count_is_written_before_the_stages(r2):
    r2[0].write("dpi", {"stages": {7: {"x": 1000}}, "count": 7, "active": 2})
    assert [c for c in r2[1].sent_cmds() if c in (12484, 12486, 12487)] == [12486, 12484, 12487]


def test_one_rejected_stage_does_not_block_the_rest(r2):
    r2[1].dpi_limit = 26000
    with pytest.raises(AmError, match="stage 8.*rejected|rejected.*stage 8"):
        r2[0].write("dpi", {"stages": {7: {"x": 30000}, 6: {"x": 5000}, 5: {"colour": "#112233"}},
                            "count": 7, "active": 2, "button": False}, strict=False)
    d = r2[0].read("dpi")
    assert d["stages"][6]["x"] == 5000 and d["stages"][5]["colour"] == "#112233"
    assert (d["count"], d["active"], d["button"]) == (7, 2, False)
    assert d["stages"][7]["x"] == 30000              # untouched: the mouse kept what it had


def test_rewriting_a_stage_with_its_own_value_is_sent(r2):
    r2[1].dpi_limit = 26000
    with pytest.raises(AmError, match="rejected"):
        r2[0].write("dpi", {"stages": {7: {"x": 30000, "y": 30000}}})
