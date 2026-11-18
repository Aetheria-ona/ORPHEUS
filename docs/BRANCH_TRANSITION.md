# Rust as the default branch

On November 19, 2026 at 12:00 UTC (14:00 Helsinki), the maintainer plans to set the existing `rust` branch as the repository default. A fresh clone will then check out the Rust developer preview. Both implementations continue to accept contributions.

| Work | Pull request base |
| --- | --- |
| Python reference server, launcher and extraction tooling | `main` |
| Native Rust server | `rust` |

For Python work after the switch, choose **base: main** when opening a pull request. To clone Python directly, use `git clone --branch main https://github.com/leafall903/ORPHEUS.git`. Existing local branches continue to track their configured upstreams. To refresh an existing clone's local `origin/HEAD` pointer, use `git remote set-head origin -a`.

## Maintainer verification

1. Before the window, run `git ls-remote --symref origin HEAD refs/heads/main refs/heads/rust` and record the default and both branch tips. Check the current base of pending Python and Rust pull requests.
2. At the scheduled time, use the repository's **Settings → General → Default branch** selector to choose the existing `rust` branch. This is the only planned settings change.
3. Rerun the command from step 1. The `HEAD` line should name `refs/heads/rust`. Verify both named branches are accessible and pending PRs have their intended bases and normal required checks. Record the time of the observed switch.

The default identifies which branch GitHub displays and offers as a PR base. Gameplay evidence continues to be tracked by implementation, commit and runtime manifest.
