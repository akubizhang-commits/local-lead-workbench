"""S3 upload of daily backups (AWS trial, design §5.2).

Credentials come only from boto3's default chain (the EC2 instance role via IMDSv2);
nothing here reads or passes keys. boto3 is imported on first use, so local mode
never needs it. ``endpoint`` exists for tests only (path-style S3 stand-in).
"""
from __future__ import annotations
from datetime import datetime, timezone
import logging

for _name in ("botocore", "boto3", "s3transfer", "urllib3"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class S3Uploader:
    def __init__(self, bucket, prefix, region, endpoint=None):
        self.bucket, self.prefix, self.region, self.endpoint = bucket, prefix, region, endpoint or None
        self._client = None

    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config
            options = {"retries": {"max_attempts": 3, "mode": "standard"}, "connect_timeout": 10,
                       "read_timeout": 60, "request_checksum_calculation": "when_required"}
            if self.endpoint:
                options["s3"] = {"addressing_style": "path"}
            self._client = boto3.client("s3", region_name=self.region, endpoint_url=self.endpoint,
                                        config=Config(**options))
        return self._client

    def key(self, name, now=None):
        moment = now or datetime.now(timezone.utc)
        return f"{self.prefix}{moment:%Y}/{moment:%m}/{name}"

    def upload(self, fileobj, name) -> str:
        from boto3.s3.transfer import TransferConfig
        key = self.key(name)
        self.client().upload_fileobj(fileobj, self.bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"},
                                     Config=TransferConfig(multipart_threshold=5 * 1024 ** 3, use_threads=False))
        return key


def from_config(cfg):
    if not cfg.s3_bucket:
        return None
    return S3Uploader(cfg.s3_bucket, cfg.s3_prefix, cfg.s3_region, cfg.s3_endpoint or None)
