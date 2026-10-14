from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cyberrange", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="wireguardprofile",
            name="active",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="wireguardprofile",
            name="last_handshake_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
