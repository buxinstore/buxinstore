# Running Database Migration on Render with Neon

## Automatic Migration (Already Configured)

Your `render.yaml` already has:
```yaml
releaseCommand: python -m alembic upgrade head
```

This means migrations run automatically when you deploy to Render.

## Manual Migration (If Needed)

### Option 1: Using Render Shell (Recommended)

1. Go to your Render Dashboard: https://dashboard.render.com
2. Select your `buxinstore` service
3. Click on "Shell" tab (or "Console")
4. Run:
   ```bash
   python -m alembic upgrade head
   ```
   Or:
   ```bash
   flask db upgrade
   ```

### Option 2: Using Render CLI

If you have Render CLI installed:
```bash
render run flask db upgrade
```

### Option 3: Check Migration Status

To see current migration status:
```bash
python -m alembic current
```

To see migration history:
```bash
python -m alembic history
```

## Verify Migration Success

After migration, verify the `delivery_rule` table was created:

1. Connect to your Neon database
2. Run this SQL query:
   ```sql
   SELECT * FROM delivery_rule LIMIT 1;
   ```

Or check the table structure:
```sql
\d delivery_rule
```

## Troubleshooting

If migration fails:
1. Check Render logs for error messages
2. Verify `DATABASE_URL` environment variable is set correctly
3. Ensure Neon database is accessible
4. Check that all dependencies are installed in `requirements.txt`

