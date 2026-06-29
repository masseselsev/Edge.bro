# Design Spec: Default CPU Quota Adjustments

## Goal
Improve Borg backup performance by default by increasing the default CPU quota from `10%` to `30%`.

## Architecture & Components

1. **SQLAlchemy Models (`models.py`)**:
   - Update `Settings.default_cpu_quota` to have a default value of `30`.
2. **Pydantic Schemas (`schemas.py`)**:
   - Update `SettingsBase.default_cpu_quota` default field value to `30`.
3. **Database Upgrade (`main.py`)**:
   - Update the `upgrade_settings(db: Session)` function to automatically migrate any existing database settings where `default_cpu_quota == 10` to `30`.
4. **Unit Tests (`tests/test_db.py`)**:
   - Update the default settings test assertions to verify that the default is `30`.

## Verification Plan
1. Run `pytest tests/test_db.py` to verify DB defaults.
2. Verify that startup database upgrade code executes successfully.
