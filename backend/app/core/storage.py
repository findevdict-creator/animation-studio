"""Object storage behind a small interface (architecture section 10).

Two buckets, so that write-once can be enforced by credentials on any S3 store:

* ``library`` holds published, immutable files. Only the publisher identity can write it,
  and it writes each key once (``put_immutable``).
* ``work`` holds drafts, uploads, job outputs and renders. Workers write here.

Every object carries its SHA-256 in metadata (``x-amz-meta-sha256``); reads can verify it.
Business logic never sees boto3 or AWS specifics; it gets a ``Storage``.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import Settings, get_settings

log = logging.getLogger("studio.storage")

BucketName = Literal["library", "work"]
CHUNK = 64 * 1024 * 1024  # multipart part size, and the single-PUT threshold
_HEX = set("0123456789abcdef")


class StorageError(Exception):
    pass


class ObjectExists(StorageError):
    """A write-once key already holds different content."""


class ChecksumMismatch(StorageError):
    """Content does not match the expected SHA-256."""


class ObjectMissing(StorageError):
    pass


@dataclass(frozen=True)
class ObjectInfo:
    bucket: str
    key: str
    size: int
    sha256: str | None
    content_type: str | None
    etag: str | None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _check_sha(value: str) -> str:
    if len(value) != 64 or not set(value) <= _HEX:
        raise ValueError(f"not a lowercase hex SHA-256: {value!r}")
    return value


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or "\\" in key or any(p in ("", ".", "..") for p in key.split("/")):
        raise ValueError(f"unsafe storage key: {key!r}")
    return key


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


class Storage:
    """One bucket. Construct with ``Storage.for_bucket``."""

    def __init__(self, client: Any, bucket: str, conditional_writes: bool = True, presign_client: Any = None,
                 presign_expiry_s: int = 900):
        self.client = client
        self.bucket = bucket
        self.conditional_writes = conditional_writes
        self.presign_client = presign_client or client
        self.presign_expiry_s = presign_expiry_s

    @classmethod
    def for_bucket(cls, which: BucketName, settings: Settings | None = None, **client_kw: Any) -> "Storage":
        s = settings or get_settings()
        bucket = s.s3_library_bucket if which == "library" else s.s3_work_bucket
        client = make_client(s, **client_kw)
        presign = make_client(s, endpoint_url=s.s3_public_endpoint_url, **client_kw) if s.s3_public_endpoint_url else client
        return cls(client, bucket, s.s3_conditional_writes, presign, s.s3_presign_expiry_s)

    # ------------------------------------------------------------- reading
    def head(self, key: str) -> ObjectInfo | None:
        try:
            r = self.client.head_object(Bucket=self.bucket, Key=_check_key(key))
        except ClientError as exc:
            if _code(exc) in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return ObjectInfo(
            bucket=self.bucket,
            key=key,
            size=int(r["ContentLength"]),
            sha256=(r.get("Metadata") or {}).get("sha256"),
            content_type=r.get("ContentType"),
            etag=r.get("ETag"),
        )

    def get_file(self, key: str, dest: Path, expected_sha256: str | None = None) -> ObjectInfo:
        """Download to ``dest`` (atomically) and verify the hash if one is given or recorded."""
        info = self.head(key)
        if info is None:
            raise ObjectMissing(f"{self.bucket}/{key}")
        want = expected_sha256 or info.sha256
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        self.client.download_file(self.bucket, key, str(tmp))
        if want is not None:
            got = sha256_file(tmp)
            if got != want:
                tmp.unlink(missing_ok=True)
                raise ChecksumMismatch(f"{self.bucket}/{key}: sha256 {got} != expected {want}")
        os.replace(tmp, dest)
        return info

    def get_bytes(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"].read()
        except ClientError as exc:
            if _code(exc) in ("404", "NoSuchKey"):
                raise ObjectMissing(f"{self.bucket}/{key}") from exc
            raise

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys += [o["Key"] for o in page.get("Contents", [])]
        return keys

    # ------------------------------------------------------------- writing
    def put_file(self, key: str, path: Path, content_type: str, sha256: str | None = None) -> ObjectInfo:
        """Mutable write (work bucket): drafts, job outputs, uploads."""
        _check_key(key)
        sha = _check_sha(sha256) if sha256 else sha256_file(path)
        self._upload(key, path, content_type, sha, conditional=False)
        return ObjectInfo(self.bucket, key, path.stat().st_size, sha, content_type, None)

    def put_immutable(self, key: str, path: Path, content_type: str, sha256: str) -> ObjectInfo:
        """Write ``key`` once. Re-writing identical content is a no-op (so a resumed publish
        succeeds); different content raises ``ObjectExists``."""
        _check_key(key)
        sha = _check_sha(sha256)
        actual = sha256_file(path)
        if actual != sha:
            raise ChecksumMismatch(f"{path}: sha256 {actual} != declared {sha}")
        existing = self.head(key)
        if existing is not None:
            return self._same_or_raise(existing, sha)
        try:
            self._upload(key, path, content_type, sha, conditional=True)
        except ObjectExists:
            existing = self.head(key)
            if existing is None:
                raise
            return self._same_or_raise(existing, sha)
        return ObjectInfo(self.bucket, key, path.stat().st_size, sha, content_type, None)

    def _same_or_raise(self, existing: ObjectInfo, sha: str) -> ObjectInfo:
        if existing.sha256 == sha:
            return existing
        raise ObjectExists(f"{self.bucket}/{existing.key} already holds sha256 {existing.sha256}, refusing {sha}")

    def _upload(self, key: str, path: Path, content_type: str, sha: str, conditional: bool) -> None:
        cond = {"IfNoneMatch": "*"} if conditional and self.conditional_writes else {}
        size = path.stat().st_size
        meta = {"ContentType": content_type, "Metadata": {"sha256": sha}}
        try:
            if size <= CHUNK:
                with open(path, "rb") as f:
                    self.client.put_object(Bucket=self.bucket, Key=key, Body=f, **meta, **cond)
                return
            self._multipart(key, path, meta, cond)
        except ClientError as exc:
            if _code(exc) in ("PreconditionFailed", "412", "ConditionalRequestConflict"):
                raise ObjectExists(f"{self.bucket}/{key} was written concurrently") from exc
            raise

    def _multipart(self, key: str, path: Path, meta: dict[str, Any], cond: dict[str, str]) -> None:
        upload = self.client.create_multipart_upload(Bucket=self.bucket, Key=key, **meta)
        uid = upload["UploadId"]
        parts = []
        try:
            with open(path, "rb") as f:
                for number, block in enumerate(iter(lambda: f.read(CHUNK), b""), start=1):
                    r = self.client.upload_part(Bucket=self.bucket, Key=key, UploadId=uid, PartNumber=number, Body=block)
                    parts.append({"ETag": r["ETag"], "PartNumber": number})
            self.client.complete_multipart_upload(
                Bucket=self.bucket, Key=key, UploadId=uid, MultipartUpload={"Parts": parts}, **cond
            )
        except BaseException:
            try:
                self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=uid)
            except ClientError:
                log.warning("could not abort multipart upload %s for %s", uid, key)
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_check_key(key))

    # ------------------------------------------------------------ presign
    def presign_get(self, key: str, filename: str | None = None, expires_s: int | None = None) -> str:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": _check_key(key)}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self.presign_client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_s or self.presign_expiry_s
        )

    def presign_put(self, key: str, content_type: str, expires_s: int | None = None) -> str:
        return self.presign_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": _check_key(key), "ContentType": content_type},
            ExpiresIn=expires_s or self.presign_expiry_s,
        )

    def ping(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)


def make_client(settings: Settings, endpoint_url: str | None = None, access_key: str | None = None,
                secret_key: str | None = None) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=access_key or settings.s3_access_key or None,
        aws_secret_access_key=secret_key or settings.s3_secret_key or None,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"mode": "standard"}),
    )


def ensure_buckets(client: Any, names: list[str]) -> None:
    """Create buckets that do not exist yet (dev and test setup; production buckets are
    created by infrastructure code)."""
    for name in names:
        try:
            client.head_bucket(Bucket=name)
        except ClientError:
            client.create_bucket(Bucket=name)

