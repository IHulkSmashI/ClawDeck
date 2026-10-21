# Working on ClawDeck (for Claude sessions)

Read README.md, CONTRIBUTING.md ("Keeping up with upstream"), STATUS.md and docs/DESIGN.md before changing anything.

## Identity
- Commit as `IHulkSmashI <227083792+IHulkSmashI@users.noreply.github.com>`. Never put a real name or personal email
  in files, commits, PRs or issues.

## Merging (standing permission from the maintainer, 2026-10-21)
- Merge your own PR without asking when: every check is green on the latest commit, it is mergeable, and no review
  thread is open.
- Merge with the **rebase** method (`merge_method: rebase`), never a merge commit: GitHub stamps the account's main
  email on API merge commits.
- Never force-push or rewrite `main` without asking. Never merge someone else's PR (Dependabot included) without the
  maintainer: report it instead.

## Releases (what users download)
- Never create a tag or release on your own. A release waits until the maintainer has tested the build on a Claw.
- Changes that need hardware testing (power, drivers, anything that writes into game folders) can be merged, but
  list them in STATUS.md under "Needs the maintainer" so they are tested before the next release.

## Rules of the plugin
Battery first, every sysfs write revertible, every control explained (Hint), every UI call wired and tested, nothing
duplicating what Steam / InputPlumber / Handheld Daemon already do, credit every upstream project in CREDITS.md.
