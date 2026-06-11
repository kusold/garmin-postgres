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

    def get_activities_by_date(self, startdate: str, enddate: str) -> list[dict]:
        """Fetch all activities in a date range.

        The underlying library auto-paginates (fetches 20 at a time).

        Args:
            startdate: Start date in YYYY-MM-DD format.
            enddate: End date in YYYY-MM-DD format.

        Returns:
            List of raw activity dicts. May be empty.
        """
        logger.debug("Fetching activities from %s to %s", startdate, enddate)
        return self._garmin.get_activities_by_date(startdate, enddate)

    def get_activity(self, activity_id: str) -> dict:
        """Fetch detailed data for a single activity.

        This hits /activity-service/activity/{id}, which returns significantly
        richer data than the list endpoint used by get_activities_by_date()
        (splits/laps, weather, HR zones, training effect, etc.).

        Args:
            activity_id: Garmin activity ID.

        Returns:
            Raw API response dict with full activity detail.
        """
        logger.debug("Fetching detailed activity %s", activity_id)
        return self._garmin.get_activity(activity_id)

    def download_activity(self, activity_id: str, dl_fmt: str = "original") -> bytes:
        """Download an activity file in the specified format.

        Args:
            activity_id: Garmin activity ID.
            dl_fmt: One of 'original', 'tcx', 'gpx'.

        Returns:
            Raw bytes of the downloaded file (ZIP for original/FIT).
        """
        fmt_map = {
            "original": Garmin.ActivityDownloadFormat.ORIGINAL,
            "tcx": Garmin.ActivityDownloadFormat.TCX,
            "gpx": Garmin.ActivityDownloadFormat.GPX,
        }
        fmt_enum = fmt_map.get(dl_fmt, Garmin.ActivityDownloadFormat.ORIGINAL)
        logger.debug("Downloading activity %s in %s format", activity_id, dl_fmt)
        return self._garmin.download_activity(activity_id, dl_fmt=fmt_enum)
