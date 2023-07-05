import djclick as click

from fastcore.net import HTTP422UnprocessableEntityError
from libraries.github import GithubAPIClient, GithubDataParser, LibraryUpdater
from libraries.models import Library, LibraryVersion
from libraries.utils import parse_date
from versions.models import Version


@click.command()
@click.option("--token", is_flag=False, help="Github API token")
@click.option("--release", is_flag=False, help="Boost version number (example: 1.81.0)")
def command(release, token):
    """Cycles through all Versions in the database, and for each version gets the
    corresponding tag's .gitmodules.

    The command then goes to the same tag of the repo for each library in the
    .gitmodules file and uses the information to create LibraryVersion instances, and
    add maintainers to LibraryVersions.

    Args:
        token (str): Github API token, if a value other than the setting is needed.
        release (str): Boost version number (example: 1.81.0). If a partial version
            number is provided, the command process all versions that contain the
            partial version number (example: "--version="1.7" would process 1.7.0,
            1.7.1, 1.7.2, etc.)
    """
    client = GithubAPIClient(token=token)
    parser = GithubDataParser()
    updater = LibraryUpdater(client=client)

    skipped = []

    if release is None:
        versions = Version.objects.active()
    else:
        versions = Version.objects.filter(name__icontains=release)

    for version in versions:
        click.echo(f"Processing version {version.name}...")
        ref = client.get_ref(ref=version.name)
        try:
            raw_gitmodules = client.get_gitmodules(ref=ref)
        except HTTP422UnprocessableEntityError as e:
            # Only happens for one version; uncertain why.
            click.secho(f"Could not get gitmodules for {version.name}.", fg="red")
            skipped.append({"version": version.name, "reason": str(e)})
            continue

        gitmodules = parser.parse_gitmodules(raw_gitmodules.decode("utf-8"))

        for gitmodule in gitmodules:
            library_name = gitmodule["module"]
            click.echo(f"Processing module {library_name}...")

            if library_name in updater.skip_modules:
                click.echo(f"Skipping module {library_name}.")
                continue

            libraries_json = client.get_libraries_json(repo_slug=library_name)
            github_data = client.get_repo(repo_slug=library_name)
            extra_data = {
                "last_github_update": parse_date(github_data.get("updated_at", "")),
                "github_url": github_data.get("html_url", ""),
            }

            # If the libraries.json file exists, we can use it to get the library info
            if libraries_json:
                libraries = (
                    libraries_json
                    if isinstance(libraries_json, list)
                    else [libraries_json]
                )
                parsed_libraries = [
                    parser.parse_libraries_json(lib) for lib in libraries
                ]
                for lib_data in parsed_libraries:
                    lib_data.update(extra_data)
                    library = updater.update_library(lib_data)
                    library_version = handle_library_version(
                        version, library, lib_data["maintainers"], updater
                    )
                    if not library_version:
                        click.secho(
                            f"Could not save library version {lib_data['name']}.",
                            fg="red",
                        )
                        skipped.append(
                            {
                                "version": version.name,
                                "library": lib_data["name"],
                                "reason": "Could not save library version",
                            }
                        )
            else:
                # This can happen with older tags; the libraries.json file didn't always
                # exist, so when it isn't present, we search for the library by the
                # module name and try to save the LibraryVersion that way.
                click.echo(
                    f"Could not get libraries.json for {lib_data['name']}; will try to "
                    f"save by gitmodule name."
                )
                try:
                    library = Library.objects.get(name=lib_data["name"])
                    library_version = handle_library_version(
                        version, library, [], updater
                    )
                except Library.DoesNotExist:
                    click.secho(
                        f"Could not save library version {lib_data['name']}.", fg="red"
                    )
                    skipped.append(
                        {
                            "version": version.name,
                            "library": lib_data["name"],
                            "reason": "Could not save library version",
                        }
                    )
                    continue

    skipped_messages = [
        f"Skipped {obj['library']} in {obj['version']}: {obj['reason']}"
        if "library" in obj
        else f"Skipped {obj['version']}: {obj['reason']}"
        for obj in skipped
    ]

    for message in skipped_messages:
        click.secho(message, fg="red")


def handle_library_version(version, library, maintainers, updater):
    """Handles the creation and updating of a LibraryVersion instance."""
    library_version, created = LibraryVersion.objects.get_or_create(
        version=version, library=library
    )
    click.secho(
        f"Saved library version {library_version}. Created? {created}", fg="green"
    )

    if created:
        updater.update_maintainers(library_version, maintainers=maintainers)
        click.secho(f"Updated maintainers for {library_version}.", fg="green")

    return library_version
