# Generated by Django 4.2.2 on 2023-08-31 22:46

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("libraries", "0013_library_featured"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="library",
            name="closed_prs_per_month",
        ),
        migrations.RemoveField(
            model_name="library",
            name="commits_per_release",
        ),
        migrations.RemoveField(
            model_name="library",
            name="first_release",
        ),
        migrations.RemoveField(
            model_name="library",
            name="last_github_update",
        ),
        migrations.RemoveField(
            model_name="library",
            name="open_issues",
        ),
    ]
