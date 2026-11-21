# First package release

At the time of preparation, npm returns 404 for `apex-flow-web-scraper` and PyPI has no matching public release. The Python wheel and npm package both build locally from this repository at version `0.11.0`.

## Registry setup

Before a first release, configure trusted publishing for this repository:

- PyPI: create a pending publisher for owner `Apexflowlabsllc`, repository `apex-flow-web-scraper`, workflow `publish-packages.yml`, environment `pypi`.
- npm: make the first `apex-flow-web-scraper` publish from the package owner's authenticated npm session, then add a GitHub Actions trusted publisher for this repository and workflow `publish-packages.yml`. npm requires the package to exist before its trusted publisher can be configured. Never put an npm token in the repository or chat.

The npm publisher uses Node 24 and npm 11.5.1+ so it can authenticate with npm's OIDC trusted publisher. Neither registry uses a long-lived publish token in GitHub Actions.

## Release steps

1. Merge the reviewed package changes to `main`.
2. Configure the PyPI pending publisher and make the one-time npm publish from the package owner's authenticated npm session.
3. Add both trusted publishers as above.
4. Run **Publish scraper packages** from GitHub Actions on `main`.
5. Leave each publish input unchecked for a build-only run. Check only the registry whose release is approved.
6. Confirm the package page and install instructions resolve, then update the version before the next release.

The workflow is manual, restricted to `main`, and both publish inputs default to `false`.
