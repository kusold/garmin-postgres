from garmin_postgres.sync_state import SyncStateStore


def test_sync_state_is_scoped_to_destination_and_item(session):
    store = SyncStateStore(session)
    store.put("notion", "item-1", {"properties": {"name": "A"}})
    store.put("another-destination", "item-1", {"title": "Other"})
    store.put("notion", "item-1", {"properties": {"name": "B"}})

    assert store.get("notion", "item-1") == {"properties": {"name": "B"}}
    assert store.get("another-destination", "item-1") == {"title": "Other"}
    assert store.get("notion", "missing") is None

    snapshot = store.get("notion", "item-1")
    snapshot["properties"]["name"] = "Unwritten edit"
    assert store.get("notion", "item-1")["properties"]["name"] == "B"
