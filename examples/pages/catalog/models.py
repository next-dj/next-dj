from django.db import models


class Product(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField()

    class Meta:
        app_label = "catalog"

    def __str__(self) -> str:
        return self.title
