# Database migrations

Apply migrations from the repository root with:

```bash
python -m alembic -c migrations/alembic.ini upgrade head
```

Always back up the database and verify the audit chain before upgrading production. Never edit an
applied revision; add a new migration instead. Test downgrades only against disposable synthetic
databases unless an explicit recovery procedure has been approved.
