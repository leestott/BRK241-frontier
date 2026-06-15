from fibreops.tools.knowledge import lookup_sop, lookup_node
from fibreops.tools.dispatch import find_best_engineer
from fibreops.tools.memory import remember, recall


def test_lookup_sop_returns_loss_of_light_for_los():
    sop = lookup_sop(signal_type="loss_of_light")
    assert sop["id"] == "sop_loss_of_light"
    assert "Splicing-capable" in sop["text"]


def test_lookup_node_returns_topology():
    node = lookup_node("FN-LDN-001")
    assert node["region"] == "London"
    assert node["customers_served"] > 0


def test_find_best_engineer_prefers_on_shift_in_region():
    res = find_best_engineer("FN-LDN-001", ["splicing"])
    assert res["engineer"] is not None
    assert res["engineer"]["region"] == "London"
    assert res["engineer"]["on_shift"] is True
    assert res["eta_minutes"] >= 15


def test_memory_round_trip(tmp_path, monkeypatch):
    from fibreops.tools import memory as mem
    monkeypatch.setattr(mem, "_DB_PATH", tmp_path / "mem.db")
    remember("test", "k1", {"v": 1})
    remember("test", "k1", {"v": 2})
    out = recall("test", "k1")
    assert out[0]["value"]["v"] == 2
