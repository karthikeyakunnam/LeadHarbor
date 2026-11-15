#!/usr/bin/env python3
"""Idempotent synthetic fixtures. Refuses to run against a non-demo configuration."""
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leadharbor.config import Settings
from leadharbor.db import Store
from leadharbor.security import normalize_lead
from leadharbor.worker import Worker


def main():
    settings = Settings.from_env()
    if not settings.demo:
        raise SystemExit('Demo fixtures require DEMO_MODE=true. No records were written.')
    store = Store(settings.db_path)
    worker = Worker(store, settings)
    fixtures = [
        ('Alex Morgan', 'alex@northstar.example.com', 'We need help deploying our software product this week. The budget is approved.', 'hot', 9, 'qualified'),
        ('Priya Shah', 'priya@paperplane.example.com', 'We are planning an AI support feature and would like to explore implementation options.', 'warm', 7, 'contacted'),
        ('Sam Rivera', 'sam@fieldwork.example.com', 'Our team needs an internal operations dashboard. Could we discuss the scope?', 'warm', 6, 'new'),
        ('Taylor Chen', 'taylor@brightpath.example.com', 'We want to automate our customer onboarding and connect our existing tools.', 'hot', 8, 'new'),
        ('Jordan Lee', 'jordan@sunroom.example.com', 'I am seeking a job and would love to share my resume for future opportunities.', 'cold', 2, 'lost'),
        ('Casey Brooks', 'casey@oakstudio.example.com', 'We are interested in a short discovery project to review our application architecture.', 'warm', 7, 'won'),
        ('Riley Park', 'riley@signal.example.com', 'I would like to learn more about the services your team provides for businesses.', 'review', 5, 'new'),
        ('Avery Green', 'avery@tideline.example.com', 'We have an urgent integration project and need engineering help with the API this week.', 'hot', 9, 'new'),
    ]
    for i, (name, email, message, tier, score, stage) in enumerate(fixtures):
        row, created = store.capture(normalize_lead({'full_name': name, 'email': email, 'message': message}), f'demo-fixture-{i:03d}', 'synthetic_demo', True)
        if not created:
            continue
        worker.process_one()
        # Explicitly labeled fixture outcomes illustrate the UI; these are not real model scores.
        with store.transaction() as db:
            db.execute('''UPDATE leads SET tier=?,score=?,stage=?,status=?,owner=?,created_at=?,
                       follow_up_at=?,score_accepted=? WHERE id=?''',
                       (tier, score, stage, 'needs_review' if tier == 'review' else 'ready', 'Sales' if tier=='hot' else 'Unassigned',
                        time.time() - i * 7200, time.time() + (i - 2) * 3600, 1 if i in (0,1,5) else None, row['id']))
    print('Synthetic demo is ready. Existing fixture records were preserved. No external requests were made.')


if __name__ == '__main__':
    main()
