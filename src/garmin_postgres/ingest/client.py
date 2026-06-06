import logging

from garminconnect import Garmin

logger = logging.getLogger(__name__)


class GarminClient:
    """Thin wrapper around python-garminconnect's Garmin client.

    Provides a clean interface for fetching data types used by the
    ingestion pipeline. The underlying Garmin instance is accessible
    via the `garmin` property for token management.
    """

    def __init__(self, garmin: Garmin) -> None:
        self._garmin = garmin

    @property
    def garmin(self) -> Garmin:
        """Access the underlying Garmin client for token operations."""
        return self._garmin

    def get_daily_summary(self, cdate: str) -> dict:
        """Fetch daily health summary for a date.

        Args:
            cdate: Date in YYYY-MM-DD format.

        Returns:
            Raw API response dict containing steps, calories, heart rate,
            stress, body battery, and intensity minutes.
        """
        logger.debug("Fetching daily summary for %s", cdate)
        return self._garmin.get_user_summary(cdate)
