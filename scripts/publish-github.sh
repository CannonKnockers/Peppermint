#!/usr/bin/env bash
# Create the private GitHub repository, push this checkout, and create a roadmap.
set -euo pipefail

OWNER="${GITHUB_OWNER:-jescolmax}"
REPO="${GITHUB_REPOSITORY:-peppermint}"
PROJECT_TITLE="${GITHUB_PROJECT_TITLE:-Peppermint roadmap}"

command -v gh >/dev/null 2>&1 || {
    echo "GitHub CLI is required. Install it from https://cli.github.com/." >&2
    exit 1
}
gh auth status >/dev/null

if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
    echo "Repository already exists: https://github.com/$OWNER/$REPO"
    if ! git remote get-url origin >/dev/null 2>&1; then
        git remote add origin "git@github.com:$OWNER/$REPO.git"
    fi
else
    gh repo create "$OWNER/$REPO" --private --source=. --remote=origin --push \
        --description "A local AI helper for the Linux Mint desktop"
fi

if ! gh project list --owner "$OWNER" --format json --jq \
    ".projects[] | select(.title == \"$PROJECT_TITLE\") | .number" | grep -q .; then
    gh project create --owner "$OWNER" --title "$PROJECT_TITLE"
else
    echo "Project already exists: $PROJECT_TITLE"
fi

echo "GitHub setup complete:"
echo "  repository: https://github.com/$OWNER/$REPO"
echo "  project:    $PROJECT_TITLE"
