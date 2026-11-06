import pytest

from mediamind import plans, settings, tools

from .conftest import HAVE_EXIFTOOL

pytestmark = pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")


def ctx(repo, **agents):
    s = settings.load()
    s["agents"].update(agents)
    return tools.ToolContext(source="assistant", default_repo=repo.id, settings=s)


def test_search_and_facets(tracked_repo):
    c = ctx(tracked_repo)
    r = tools.run(c, "search_media", {"year": 2024})
    assert r["total"] == 3  # IMG_1, kopyası, IMG_2
    assert all("_blake3" in i for i in r["items"])
    assert "_blake3" not in tools.to_json(r)  # iç alanlar ajana gitmez
    r = tools.run(c, "search_media", {"date_from": "2023-07-01", "date_to": "2023-07-02"})
    assert [i["rel_path"] for i in r["items"]] == ["DCIM/IMG_3.jpg"]
    place = r["items"][0]["place"]
    assert tools.run(c, "search_media", {"place": place[:4]})["total"] >= 1
    f = tools.run(c, "list_facets", {})
    assert {y["year"] for y in f["years"]} == {"2023", "2024"}
    assert c.shown_files == [] or isinstance(c.shown_files, list)
    assert tools.run(c, "search_media", {"date_source": "estimated"})["total"] == 0
    assert tools.run(c, "search_media", {"date_source": "exif"})["total"] == 4


def test_tags_rating_and_search_by_tag(tracked_repo):
    c = ctx(tracked_repo)
    items = tools.run(c, "search_media", {"year": 2024})["items"]
    ids = [next(i["file_id"] for i in items if i["rel_path"] == "DCIM/IMG_1.jpg")]
    assert tools.run(c, "update_media", {"file_ids": ids, "add_tags": ["tatil"], "rating": 4})["updated_files"] == 1
    r = tools.run(c, "search_media", {"tag": "tatil"})
    assert r["total"] == 2  # aynı içerikli kopya da etiketi paylaşır (etiket içeriğe bağlı)
    assert r["items"][0]["tags"] == ["tatil"]
    g = tools.run(c, "get_media", {"file_ids": ids})["items"][0]
    assert g["rating"] == 4 and g["tags"] == ["tatil"]
    assert g["identical_copies"] == ["Downloads/IMG_1 (1).jpg"]
    tools.run(c, "update_media", {"file_ids": ids, "remove_tags": ["tatil"]})
    assert tools.run(c, "search_media", {"tag": "tatil"})["total"] == 0
    with pytest.raises(tools.ToolError):
        tools.run(ctx(tracked_repo, tag=False), "update_media", {"file_ids": ids, "add_tags": ["x"]})


def test_plans_need_approval(tracked_repo):
    c = ctx(tracked_repo)
    ids = [i["file_id"] for i in tools.run(c, "search_media", {"year": 2024, "folder": "DCIM"})["items"]]
    r = tools.run(c, "create_plan", {"kind": "move", "file_ids": ids, "destination_folder": "Bodrum 2024"})
    assert r["status"] == "pending" and r["files"] == 2
    assert plans.get(tracked_repo, r["plan_id"])["source"] == "assistant"
    a = tools.run(c, "apply_plan", {"plan_id": r["plan_id"]})
    assert a["applied"] is False
    assert (tracked_repo.root / "DCIM" / "IMG_1.jpg").exists()

    free = ctx(tracked_repo, require_approval=False)
    a = tools.run(free, "apply_plan", {"plan_id": r["plan_id"]})
    assert a["status"] == "applied"
    assert (tracked_repo.root / "Bodrum 2024" / "IMG_1.jpg").exists()
    assert tools.run(free, "undo_plan", {"plan_id": r["plan_id"]})["status"] == "undone"


def test_dedup_plan_and_permissions(tracked_repo):
    c = ctx(tracked_repo)
    d = tools.run(c, "find_duplicates", {})
    assert d["files_suggested_for_deletion"] == 1
    p = tools.run(c, "create_plan", {"kind": "dedup"})
    assert p["files"] == 1 and p["sample"][0]["to"] == "çöp"
    with pytest.raises(tools.ToolError):
        tools.run(c, "get_preview_image", {"file_id": 1})  # izin kapalı
    img = tools.run(ctx(tracked_repo, send_previews=True), "get_preview_image", {"file_id": 1})
    assert img["media_type"] == "image/jpeg" and img["__image__"][:2] == b"\xff\xd8"
    with pytest.raises(tools.ToolError):
        tools.run(ctx(tracked_repo, read=False), "search_media", {})
    with pytest.raises(tools.ToolError):
        tools.run(c, "create_plan", {"kind": "move", "file_ids": [1], "destination_folder": "../disari"})


def test_settings_roundtrip():
    s = settings.update({"assistant": {"model": "claude-sonnet-5-5"}, "agents": {"send_previews": True}, "bogus": {"x": 1}})
    assert s["assistant"]["model"] == "claude-sonnet-5-5" and s["agents"]["send_previews"] is True
    with pytest.raises(ValueError):
        settings.update({"assistant": {"model": "gpt-x"}})
    assert settings.get_api_key() is None
    settings.set_api_key("sk-ant-test-1234567890abcd")
    assert settings.get_api_key() == "sk-ant-test-1234567890abcd"
    assert settings.masked_key() == "sk-ant-••••abcd"
    settings.set_api_key(None)
    assert settings.get_api_key() is None


def test_album_memory_cleanup_and_edit_tools(tracked_repo):
    c = ctx(tracked_repo, tag=True)
    ids = [i["file_id"] for i in tools.run(c, "search_media", {"year": 2024})["items"]]
    r = tools.run(c, "create_album", {"name": "Bodrum", "file_ids": ids[:1]})
    assert r["added"] == 1
    assert tools.run(c, "add_to_album", {"album": "bodrum", "file_ids": ids})["added"] >= 1
    albums = tools.run(c, "list_albums", {})["albums"]
    assert albums[0]["name"] == "Bodrum" and albums[0]["count"] >= 1
    assert tools.run(c, "search_media", {"album": "Bodrum"})["total"] >= 1
    sm = tools.run(c, "create_album", {"name": "2023", "kind": "smart", "rules": {"year": 2023}})
    assert sm["kind"] == "smart"
    assert "memories" in tools.run(c, "list_memories", {})
    assert "categories" in tools.run(c, "cleanup_suggestions", {})
    before = tools.run(c, "get_media", {"file_ids": ids[:1]})["items"][0]["taken_at"]
    assert tools.run(c, "fix_dates", {"file_ids": ids[:1], "shift_hours": 2})["updated"] == 1
    after = tools.run(c, "get_media", {"file_ids": ids[:1]})["items"][0]["taken_at"]
    assert after != before
    tools.run(c, "fix_dates", {"file_ids": ids[:1], "revert": True})
    assert tools.run(c, "get_media", {"file_ids": ids[:1]})["items"][0]["taken_at"] == before
    place = tools.run(c, "search_media", {"year": 2023})["items"][0]["place"]
    r = tools.run(c, "set_location", {"file_ids": ids[:1], "place": place})
    assert r["updated"] == 1
    with pytest.raises(tools.ToolError):  # izin yoksa değiştirmez
        tools.run(ctx(tracked_repo, tag=False), "create_album", {"name": "X"})
