from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_userprofile_privacy_accepted_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="service_ip",
            field=models.GenericIPAddressField(
                blank=True,
                help_text="Reserved MetalLB IPv4 address for an Application Blue cell.",
                protocol="IPv4",
            ),
        ),
        migrations.AddConstraint(
            model_name="team",
            constraint=models.UniqueConstraint(
                condition=~models.Q(service_ip=""),
                fields=("service_ip",),
                name="unique_team_service_ip",
            ),
        ),
    ]
