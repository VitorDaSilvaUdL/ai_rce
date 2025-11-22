from dataclasses import dataclass

from influxdb_client import InfluxDBClient
from redis import Redis


@dataclass(frozen=True)
class _Instances:
    redis: Redis
    influx: InfluxDBClient


class DB:
    _instances: _Instances

    @classmethod
    def _ensure(cls) -> _Instances:
        if not getattr(cls, "_instances", None):
            setattr(
                cls,
                "_instances",
                _Instances(
                    redis=Redis(
                        host="rain-proba-redis",
                        port="6379",
                    ),
                    influx=InfluxDBClient(
                        url="/influxdb/influxd.sqlite",
                        token="your_secure_token",
                        org="my_org",
                    ),
                ),
            )
        return cls._instances

    @classmethod
    def close(cls):
        if not getattr(cls, "_instances", None):
            return

        cls._instances.redis.close()
        cls._instances.influx.close()

    @classmethod
    def redis(cls) -> Redis:
        return cls._ensure().redis

    @classmethod
    def influx(cls) -> InfluxDBClient:
        return cls._ensure().influx
