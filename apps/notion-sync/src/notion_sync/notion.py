from typing import Any


class NotionSink:
    def __init__(self, client: Any, *, dry_run: bool = False) -> None:
        self.client = client
        self.dry_run = dry_run

    def upsert_page(
        self,
        database_id: str,
        *,
        filter_payload: dict,
        properties: dict,
        icon: dict | None = None,
        cover: dict | None = None,
    ) -> str:
        if self.dry_run:
            return "created"

        existing = self.client.databases.query(
            database_id=database_id,
            filter=filter_payload,
        )["results"]

        if existing:
            update_payload = {
                "page_id": existing[0]["id"],
                "properties": properties,
            }
            if icon:
                update_payload["icon"] = icon
            if cover:
                update_payload["cover"] = cover
            self.client.pages.update(**update_payload)
            return "updated"

        create_payload = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }
        if icon:
            create_payload["icon"] = icon
        if cover:
            create_payload["cover"] = cover
        self.client.pages.create(**create_payload)
        return "created"
