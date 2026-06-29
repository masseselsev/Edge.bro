# Workspace Rules

- **No Force Git Commits of Ignored Files**: Never force-add files or directories to Git that are matches for rules in `.gitignore` (e.g. do not run `git add -f` on folders like `docs/` or `.superpowers/`). Always verify `.gitignore` matches before committing.
