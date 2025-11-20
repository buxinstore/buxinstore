#!/usr/bin/env python
"""Run database migration"""
from app import app
from flask_migrate import upgrade

with app.app_context():
    print("Running database migration...")
    upgrade()
    print("Migration completed successfully!")

