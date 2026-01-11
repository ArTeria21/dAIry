"""Weather service for fetching weather data from Open-Meteo API."""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Vienna coordinates as default
VIENNA_LATITUDE = 48.2082
VIENNA_LONGITUDE = 16.3738
VIENNA_NAME = "Vienna"

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Timeout for API requests (seconds)
REQUEST_TIMEOUT = 10.0


@dataclass
class WeatherData:
    """Weather data for a specific location."""

    city: str
    temperature_max: float  # Daily max temperature in °C
    pressure: float  # Sea level pressure in hPa
    cloud_cover: int  # Cloud cover percentage (0-100)
    uv_index: float  # UV index (0-11+)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "city": self.city,
            "temperature_max": self.temperature_max,
            "pressure": self.pressure,
            "cloud_cover": self.cloud_cover,
            "uv_index": self.uv_index,
        }


@dataclass
class GeoLocation:
    """Geocoded location data."""

    name: str
    latitude: float
    longitude: float
    country: str | None = None


async def geocode_city(city_name: str) -> GeoLocation | None:
    """
    Geocode a city name to get coordinates.

    Args:
        city_name: Name of the city to geocode

    Returns:
        GeoLocation if found, None otherwise
    """
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(
                GEOCODING_URL,
                params={
                    "name": city_name,
                    "count": 1,
                    "language": "en",
                    "format": "json",
                },
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])
            if not results:
                logger.warning("No geocoding results found for city: %s", city_name)
                return None

            result = results[0]
            return GeoLocation(
                name=result.get("name", city_name),
                latitude=result["latitude"],
                longitude=result["longitude"],
                country=result.get("country"),
            )
    except httpx.HTTPError as e:
        logger.error("HTTP error during geocoding for %s: %s", city_name, e)
        return None
    except Exception as e:
        logger.error("Unexpected error during geocoding for %s: %s", city_name, e)
        return None


async def get_weather(
    latitude: float,
    longitude: float,
    city_name: str = "Unknown",
) -> WeatherData | None:
    """
    Get current day's weather data for given coordinates.

    Args:
        latitude: Location latitude
        longitude: Location longitude
        city_name: City name for the weather data

    Returns:
        WeatherData if successful, None otherwise
    """
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(
                FORECAST_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "daily": [
                        "temperature_2m_max",
                        "uv_index_max",
                    ],
                    "hourly": [
                        "pressure_msl",
                        "cloud_cover",
                    ],
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            )
            response.raise_for_status()
            data = response.json()

            # Extract daily values (first day)
            daily = data.get("daily", {})
            temperature_max = daily.get("temperature_2m_max", [None])[0]
            uv_index = daily.get("uv_index_max", [None])[0]

            # Extract hourly values - get average for noon (12:00)
            # The hourly data contains 24 values, we take the value at noon
            hourly = data.get("hourly", {})
            pressure_values = hourly.get("pressure_msl", [])
            cloud_cover_values = hourly.get("cloud_cover", [])

            # Get noon values (index 12) or average if not available
            if pressure_values:
                pressure = pressure_values[12] if len(pressure_values) > 12 else sum(pressure_values) / len(pressure_values)
            else:
                pressure = None

            if cloud_cover_values:
                cloud_cover = cloud_cover_values[12] if len(cloud_cover_values) > 12 else sum(cloud_cover_values) / len(cloud_cover_values)
            else:
                cloud_cover = None

            # Check if we have all required data
            if any(v is None for v in [temperature_max, pressure, cloud_cover, uv_index]):
                logger.warning(
                    "Incomplete weather data for %s: temp=%s, pressure=%s, cloud=%s, uv=%s",
                    city_name,
                    temperature_max,
                    pressure,
                    cloud_cover,
                    uv_index,
                )
                # Still return data with None values if some are missing
                return WeatherData(
                    city=city_name,
                    temperature_max=temperature_max or 0.0,
                    pressure=pressure or 0.0,
                    cloud_cover=int(cloud_cover) if cloud_cover else 0,
                    uv_index=uv_index or 0.0,
                )

            return WeatherData(
                city=city_name,
                temperature_max=temperature_max,
                pressure=pressure,
                cloud_cover=int(cloud_cover),
                uv_index=uv_index,
            )

    except httpx.HTTPError as e:
        logger.error("HTTP error fetching weather for %s: %s", city_name, e)
        return None
    except Exception as e:
        logger.error("Unexpected error fetching weather for %s: %s", city_name, e)
        return None


async def get_vienna_weather() -> WeatherData | None:
    """Get weather for Vienna (default location)."""
    return await get_weather(VIENNA_LATITUDE, VIENNA_LONGITUDE, VIENNA_NAME)


async def get_city_weather(city_name: str) -> WeatherData | None:
    """
    Get weather for a city by name.

    First geocodes the city, then fetches weather.

    Args:
        city_name: Name of the city

    Returns:
        WeatherData if successful, None otherwise
    """
    location = await geocode_city(city_name)
    if not location:
        return None

    return await get_weather(
        location.latitude,
        location.longitude,
        location.name,
    )
