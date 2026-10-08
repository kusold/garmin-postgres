"""Persistence for values last written to external destinations.

The store treats the value as opaque JSON. A destination adapter decides
what to record and how to compare it with the destination's current item.
"""

from copy import deepcopy

from sqlmodel import Session

from garmin_postgres.models.destination_sync_state import DestinationSyncState


class SyncStateStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, destination_key: str, item_id: str) -> dict | None:
        row = self._session.get(DestinationSyncState, (destination_key, item_id))
        return deepcopy(row.last_written_json) if row is not None else None

    def put(self, destination_key: str, item_id: str, last_written: dict) -> None:
        value = deepcopy(last_written)
        row = self._session.get(DestinationSyncState, (destination_key, item_id))
        if row is None:
            self._session.add(DestinationSyncState(
                destination_key=destination_key,
                item_id=item_id,
                last_written_json=value,
            ))
        else:
            row.last_written_json = value
        self._session.flush()
