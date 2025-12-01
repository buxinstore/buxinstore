# How to Seed Shipping Data

## Option 1: Run on Render (Recommended)

Since your app is deployed on Render, this is the easiest way:

1. Go to your Render Dashboard: https://dashboard.render.com
2. Select your `buxinstore` service
3. Click on the **"Shell"** tab (or **"Console"**)
4. Run the seed script:
   ```bash
   python scripts/seed_shipping_data.py
   ```

The script will:
- Create shipping modes (express, economy_plus, economy)
- Create shipping rules for all countries in the seed data
- Convert USD prices to GMD using the conversion rate (default: 67.0)

## Option 2: Run Locally

If you have the `DATABASE_URL` environment variable set up locally:

1. Open PowerShell or Command Prompt in your project directory
2. Set the DATABASE_URL environment variable (if not already set):
   ```powershell
   $env:DATABASE_URL="postgresql+psycopg://neondb_owner:npg_gAmJth1HP7EL@ep-cool-water-adn791ju-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
   ```
3. Run the seed script:
   ```powershell
   python scripts/seed_shipping_data.py
   ```

## What the Script Does

1. **Creates Shipping Modes** (if they don't exist):
   - `express`: DHL Express / FedEx International (3–7 days)
   - `economy_plus`: DHL eCommerce / Global Forwarding (10–20 days)
   - `economy`: AliExpress Economy Mail (20–60 days)

2. **Creates Shipping Rules** from `data/shipping_seed_data.json`:
   - For each country (GMB, SEN, CIV, MLI, BFA, SLE, UGA)
   - For each shipping mode
   - Creates 3 weight brackets: 0-0.5kg, 0.5-1.0kg, 1.0-2.0kg
   - Converts USD prices to GMD

3. **Weight Brackets**:
   - 0.0 - 0.5 kg: Base price from seed data
   - 0.5 - 1.0 kg: 1.6x the base price
   - 1.0 - 2.0 kg: 2.5x the base price

## Verify It Worked

After running the script, check the logs. You should see:
- "Seeding shipping modes..."
- "Seeding shipping rules..."
- "Shipping data seed completed!"

Then test the shipping calculation on your site - it should now find matching rules!

## Troubleshooting

If you get errors:
- Make sure the database migration has been run: `python -m alembic upgrade head`
- Check that the `data/shipping_seed_data.json` file exists
- Verify your DATABASE_URL is correct

