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
        parser.add_argument(
            "--keep-probe",
            action="store_true",
            help="Leave the probe object in the bucket so it can be seen in the web console.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List what the store actually holds, as the app's own credentials see it.",
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
            self._probe(keep=options["keep_probe"])
        if options["list"]:
            self._list()

        self._sweep()

    # ------------------------------------------------------------ round trip
    def _probe(self, keep=False):
        """Write, read back, and normally delete one small object.

        The clean-up is why a healthy bucket can still report zero bytes:
        the probe removes its own evidence, and nobody has uploaded since
        the credentials were set. `--keep-probe` leaves the object behind
        so it can be found in the bucket's web console, which is the
        check most people actually want.
        """
        name = f"healthcheck/{uuid.uuid4().hex}.txt"
        try:
            stored = default_storage.save(name, ContentFile(b"jengasec storage probe"))
            body = default_storage.open(stored, "rb").read()
            if not keep:
                default_storage.delete(stored)
        except Exception as exc:  # credentials, endpoint, permissions
            self.stdout.write(self.style.ERROR(f"  WRITE FAILED: {type(exc).__name__}: {exc}"))
            self.stdout.write(self.style.ERROR(
                "  Uploads are not reaching the store. Fix this before opening registration."
            ))
            return
        if body != b"jengasec storage probe":
            self.stdout.write(self.style.ERROR("  read back the wrong bytes"))
            return
        if keep:
            self.stdout.write(self.style.SUCCESS(
                f"  round trip OK -- probe left in the bucket at {stored}"
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                "  round trip OK (write, read, delete -- nothing left behind)"
            ))

    # ------------------------------------------------------------ listing
    def _list(self):
        """Walk the store from the root.

        Worth doing separately from the bucket's own web console: a
        Backblaze application key restricted to one bucket can upload
        happily while the browser UI, signed in as a different identity,
        shows nothing. This lists what *the application* can see, which
        is the view that decides whether a download works.
        """
        self.stdout.write("")
        self.stdout.write("Contents of the store, as the application sees it:")
        total = 0

        def walk(prefix, depth=0):
            nonlocal total
            try:
                dirs, files = default_storage.listdir(prefix)
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"  cannot list {prefix or '/'}: {type(exc).__name__}: {exc}"
                ))
                return
            pad = "  " + "  " * depth
            for name in sorted(files):
                path = f"{prefix}/{name}".lstrip("/")
                try:
                    size = default_storage.size(path)
                except Exception:
                    size = "?"
                self.stdout.write(f"{pad}{name}  ({size} bytes)")
                total += 1
            for name in sorted(dirs):
                self.stdout.write(f"{pad}{name}/")
                walk(f"{prefix}/{name}".lstrip("/"), depth + 1)

        walk("")
        self.stdout.write(f"  {total} object(s) total.")
        if not total:
            self.stdout.write(self.style.WARNING(
                "  The store is empty. If an upload has been made since the "
                "S3_* variables were set, it is not arriving here."
            ))

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
