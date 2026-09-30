"""Does the file store work, and which stored files have gone missing?

Two questions that look the same from a browser -- both show up as a 500
on a download link -- but have opposite answers. This separates them:

* a write/read/delete round trip proves the bucket credentials work, so
  uploads taken from now on will survive a deploy;
* a sweep of every FileField row reports the ones whose bytes are not in
  storage, which is what remains of uploads taken while the app was still
  writing to the host's own ephemeral disk.
"""
import uuid

from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import models


class Command(BaseCommand):
    help = "Verify the file store and list database rows whose files are missing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-probe",
            action="store_true",
            help="Do not write a probe object; only sweep existing rows.",
        )

    def handle(self, *args, **options):
        backend = settings.STORAGES["default"]["BACKEND"]
        self.stdout.write(f"Storage backend: {backend}")
        if "s3" in backend.lower():
            opts = settings.STORAGES["default"].get("OPTIONS", {})
            self.stdout.write(
                f"  bucket={opts.get('bucket_name')} endpoint={opts.get('endpoint_url')} "
                f"region={opts.get('region_name')}"
            )
        else:
            self.stdout.write(self.style.WARNING(
                "  Not object storage. On a host with an ephemeral disk every "
                "upload is deleted at the next deploy -- set the S3_* variables."
            ))

        if not options["skip_probe"]:
            self._probe()

        self._sweep()

    # ------------------------------------------------------------ round trip
    def _probe(self):
        name = f"healthcheck/{uuid.uuid4().hex}.txt"
        try:
            stored = default_storage.save(name, ContentFile(b"jengasec storage probe"))
            body = default_storage.open(stored, "rb").read()
            default_storage.delete(stored)
        except Exception as exc:  # credentials, endpoint, permissions
            self.stdout.write(self.style.ERROR(f"  WRITE FAILED: {type(exc).__name__}: {exc}"))
            self.stdout.write(self.style.ERROR(
                "  Uploads are not reaching the store. Fix this before opening registration."
            ))
            return
        ok = body == b"jengasec storage probe"
        self.stdout.write(self.style.SUCCESS("  round trip OK (write, read, delete)")
                          if ok else self.style.ERROR("  read back the wrong bytes"))

    # ------------------------------------------------------------ row sweep
    def _sweep(self):
        missing = ok = empty = 0
        for model in apps.get_models():
            fields = [f for f in model._meta.get_fields()
                      if isinstance(f, models.FileField)]
            if not fields:
                continue
            for row in model.objects.all().iterator():
                for f in fields:
                    value = getattr(row, f.name)
                    if not value:
                        empty += 1
                        continue
                    try:
                        present = default_storage.exists(value.name)
                    except Exception as exc:
                        present = False
                        self.stdout.write(self.style.ERROR(
                            f"  {type(exc).__name__} checking {value.name}: {exc}"
                        ))
                    if present:
                        ok += 1
                    else:
                        missing += 1
                        self.stdout.write(self.style.WARNING(
                            f"  MISSING {model._meta.label}.{f.name} "
                            f"pk={row.pk} -> {value.name}"
                        ))

        self.stdout.write("")
        self.stdout.write(f"Files present: {ok}   missing: {missing}   fields left blank: {empty}")
        if missing:
            self.stdout.write(self.style.WARNING(
                "Missing files cannot be recovered -- the bytes were never in this "
                "store. Ask each owner to upload again; the download pages now say "
                "so instead of returning a server error."
            ))
