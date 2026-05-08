import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="MetricSnapshot",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("kind", models.CharField(max_length=64)),
                ("key", models.CharField(max_length=200)),
                ("value", models.PositiveIntegerField()),
                ("captured_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ("-captured_at", "kind", "key"),
                "unique_together": {("kind", "key", "captured_at")},
            },
        ),
    ]
